import os
from huggingface_hub import HfApi, hf_hub_download
from apscheduler.schedulers.background import BackgroundScheduler
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

year = datetime.now().year
month = datetime.now().month

# Check if running in a Huggin Face Space
IS_SPACES = False
if os.getenv("SPACE_REPO_NAME"):
    print("Running in a Hugging Face Space 🤗")
    IS_SPACES = True

    # Setup database sync for HF Spaces
    if not os.path.exists("instance/tts_arena.db"):
        os.makedirs("instance", exist_ok=True)
        try:
            print("Database not found, downloading from HF dataset...")
            hf_hub_download(
                repo_id="TTS-AGI/database-arena-v2",
                filename="tts_arena.db",
                repo_type="dataset",
                local_dir="instance",
                token=os.getenv("HF_TOKEN"),
            )
            print("Database downloaded successfully ✅")
        except Exception as e:
            print(f"Error downloading database from HF dataset: {str(e)} ⚠️")

from flask import (
    Flask,
    render_template,
    g,
    request,
    jsonify,
    send_file,
    redirect,
    url_for,
    session,
    abort,
)
from flask_login import LoginManager, current_user
from models import *
from auth import auth, init_oauth, is_admin
from admin import admin
import os
from dotenv import load_dotenv
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
import uuid
import tempfile
import shutil
from tts import predict_tts
import random
import json
from datetime import datetime, timedelta
from flask_migrate import Migrate
import requests
import functools
import time # Added for potential retries


# Load environment variables
if not IS_SPACES:
    load_dotenv()  # Only load .env if not running in a Hugging Face Space

app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", os.urandom(24))
app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv(
    "DATABASE_URI", "sqlite:///tts_arena.db"
)
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SESSION_COOKIE_SECURE"] = True
app.config["SESSION_COOKIE_SAMESITE"] = (
    "None" if IS_SPACES else "Lax"
)  # HF Spaces uses iframes to load the app, so we need to set SAMESITE to None
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=30)  # Set to desired duration

# Force HTTPS when running in HuggingFace Spaces
if IS_SPACES:
    app.config["PREFERRED_URL_SCHEME"] = "https"

# Cloudflare Turnstile settings
app.config["TURNSTILE_ENABLED"] = (
    os.getenv("TURNSTILE_ENABLED", "False").lower() == "true"
)
app.config["TURNSTILE_SITE_KEY"] = os.getenv("TURNSTILE_SITE_KEY", "")
app.config["TURNSTILE_SECRET_KEY"] = os.getenv("TURNSTILE_SECRET_KEY", "")
app.config["TURNSTILE_VERIFY_URL"] = (
    "https://challenges.cloudflare.com/turnstile/v0/siteverify"
)

migrate = Migrate(app, db)

# Initialize extensions
db.init_app(app)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "auth.login"

# Initialize OAuth
init_oauth(app)

# Configure rate limits
limiter = Limiter(
    app=app,
    key_func=get_remote_address,
    default_limits=["200 per day", "50 per hour"],
    storage_uri="memory://",
)

# Create temp directory for audio files
TEMP_AUDIO_DIR = os.path.join(tempfile.gettempdir(), "tts_arena_audio")
os.makedirs(TEMP_AUDIO_DIR, exist_ok=True)

# Store active TTS sessions
app.tts_sessions = {}
tts_sessions = app.tts_sessions

# Store active conversational sessions
app.conversational_sessions = {}
conversational_sessions = app.conversational_sessions

# Register blueprints
app.register_blueprint(auth, url_prefix="/auth")
app.register_blueprint(admin)


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


@app.before_request
def before_request():
    g.user = current_user
    g.is_admin = is_admin(current_user)

    # Ensure HTTPS for HuggingFace Spaces environment
    if IS_SPACES and request.headers.get("X-Forwarded-Proto") == "http":
        url = request.url.replace("http://", "https://", 1)
        return redirect(url, code=301)

    # Check if Turnstile verification is required
    if app.config["TURNSTILE_ENABLED"]:
        # Exclude verification routes
        excluded_routes = ["verify_turnstile", "turnstile_page", "static"]
        if request.endpoint not in excluded_routes:
            # Check if user is verified
            if not session.get("turnstile_verified"):
                # Save original URL for redirect after verification
                redirect_url = request.url
                # Force HTTPS in HuggingFace Spaces
                if IS_SPACES and redirect_url.startswith("http://"):
                    redirect_url = redirect_url.replace("http://", "https://", 1)

                # If it's an API request, return a JSON response
                if request.path.startswith("/api/"):
                    return jsonify({"error": "Turnstile verification required"}), 403
                # For regular requests, redirect to verification page
                return redirect(url_for("turnstile_page", redirect_url=redirect_url))
            else:
                # Check if verification has expired (default: 24 hours)
                verification_timeout = (
                    int(os.getenv("TURNSTILE_TIMEOUT_HOURS", "24")) * 3600
                )  # Convert hours to seconds
                verified_at = session.get("turnstile_verified_at", 0)
                current_time = datetime.utcnow().timestamp()

                if current_time - verified_at > verification_timeout:
                    # Verification expired, clear status and redirect to verification page
                    session.pop("turnstile_verified", None)
                    session.pop("turnstile_verified_at", None)

                    redirect_url = request.url
                    # Force HTTPS in HuggingFace Spaces
                    if IS_SPACES and redirect_url.startswith("http://"):
                        redirect_url = redirect_url.replace("http://", "https://", 1)

                    if request.path.startswith("/api/"):
                        return jsonify({"error": "Turnstile verification expired"}), 403
                    return redirect(
                        url_for("turnstile_page", redirect_url=redirect_url)
                    )


@app.route("/turnstile", methods=["GET"])
def turnstile_page():
    """Display Cloudflare Turnstile verification page"""
    redirect_url = request.args.get("redirect_url", url_for("arena", _external=True))

    # Force HTTPS in HuggingFace Spaces
    if IS_SPACES and redirect_url.startswith("http://"):
        redirect_url = redirect_url.replace("http://", "https://", 1)

    return render_template(
        "turnstile.html",
        turnstile_site_key=app.config["TURNSTILE_SITE_KEY"],
        redirect_url=redirect_url,
    )


@app.route("/verify-turnstile", methods=["POST"])
def verify_turnstile():
    """Verify Cloudflare Turnstile token"""
    token = request.form.get("cf-turnstile-response")
    redirect_url = request.form.get("redirect_url", url_for("arena", _external=True))

    # Force HTTPS in HuggingFace Spaces
    if IS_SPACES and redirect_url.startswith("http://"):
        redirect_url = redirect_url.replace("http://", "https://", 1)

    if not token:
        # If AJAX request, return JSON error
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return (
                jsonify({"success": False, "error": "Missing verification token"}),
                400,
            )
        # Otherwise redirect back to turnstile page
        return redirect(url_for("turnstile_page", redirect_url=redirect_url))

    # Verify token with Cloudflare
    data = {
        "secret": app.config["TURNSTILE_SECRET_KEY"],
        "response": token,
        "remoteip": request.remote_addr,
    }

    try:
        response = requests.post(app.config["TURNSTILE_VERIFY_URL"], data=data)
        result = response.json()

        if result.get("success"):
            # Set verification status in session
            session["turnstile_verified"] = True
            session["turnstile_verified_at"] = datetime.utcnow().timestamp()

            # Determine response type based on request
            is_xhr = request.headers.get("X-Requested-With") == "XMLHttpRequest"
            accepts_json = "application/json" in request.headers.get("Accept", "")

            # If AJAX or JSON request, return success JSON
            if is_xhr or accepts_json:
                return jsonify({"success": True, "redirect": redirect_url})

            # For regular form submissions, redirect to the target URL
            return redirect(redirect_url)
        else:
            # Verification failed
            app.logger.warning(f"Turnstile verification failed: {result}")

            # If AJAX request, return JSON error
            if request.headers.get("X-Requested-With") == "XMLHttpRequest":
                return jsonify({"success": False, "error": "Verification failed"}), 403

            # Otherwise redirect back to turnstile page
            return redirect(url_for("turnstile_page", redirect_url=redirect_url))

    except Exception as e:
        app.logger.error(f"Turnstile verification error: {str(e)}")

        # If AJAX request, return JSON error
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return (
                jsonify(
                    {"success": False, "error": "Server error during verification"}
                ),
                500,
            )

        # Otherwise redirect back to turnstile page
        return redirect(url_for("turnstile_page", redirect_url=redirect_url))

with open("harvard_sentences.txt", "r") as f:
    harvard_sentences = f.readlines()

@app.route("/")
def arena():
    return render_template("arena.html", harvard_sentences=json.dumps(harvard_sentences))


@app.route("/leaderboard")
def leaderboard():
    tts_leaderboard = get_leaderboard_data(ModelType.TTS)
    conversational_leaderboard = get_leaderboard_data(ModelType.CONVERSATIONAL)
    top_voters = get_top_voters(10)  # Get top 10 voters

    # Initialize personal leaderboard data
    tts_personal_leaderboard = None
    conversational_personal_leaderboard = None
    user_leaderboard_visibility = None

    # If user is logged in, get their personal leaderboard and visibility setting
    if current_user.is_authenticated:
        tts_personal_leaderboard = get_user_leaderboard(current_user.id, ModelType.TTS)
        conversational_personal_leaderboard = get_user_leaderboard(
            current_user.id, ModelType.CONVERSATIONAL
        )
        user_leaderboard_visibility = current_user.show_in_leaderboard

    # Get key dates for the timeline
    tts_key_dates = get_key_historical_dates(ModelType.TTS)
    conversational_key_dates = get_key_historical_dates(ModelType.CONVERSATIONAL)

    # Format dates for display in the dropdown
    formatted_tts_dates = [date.strftime("%B %Y") for date in tts_key_dates]
    formatted_conversational_dates = [
        date.strftime("%B %Y") for date in conversational_key_dates
    ]

    return render_template(
        "leaderboard.html",
        tts_leaderboard=tts_leaderboard,
        conversational_leaderboard=conversational_leaderboard,
        tts_personal_leaderboard=tts_personal_leaderboard,
        conversational_personal_leaderboard=conversational_personal_leaderboard,
        tts_key_dates=tts_key_dates,
        conversational_key_dates=conversational_key_dates,
        formatted_tts_dates=formatted_tts_dates,
        formatted_conversational_dates=formatted_conversational_dates,
        top_voters=top_voters,
        user_leaderboard_visibility=user_leaderboard_visibility
    )


@app.route("/api/historical-leaderboard/<model_type>")
def historical_leaderboard(model_type):
    """Get historical leaderboard data for a specific date"""
    if model_type not in [ModelType.TTS, ModelType.CONVERSATIONAL]:
        return jsonify({"error": "Invalid model type"}), 400

    # Get date from query parameter
    date_str = request.args.get("date")
    if not date_str:
        return jsonify({"error": "Date parameter is required"}), 400

    try:
        # Parse date from URL parameter (format: YYYY-MM-DD)
        target_date = datetime.strptime(date_str, "%Y-%m-%d")

        # Get historical leaderboard data
        leaderboard_data = get_historical_leaderboard_data(model_type, target_date)

        return jsonify(
            {"date": target_date.strftime("%B %d, %Y"), "leaderboard": leaderboard_data}
        )
    except ValueError:
        return jsonify({"error": "Invalid date format. Use YYYY-MM-DD"}), 400


@app.route("/about")
def about():
    return render_template("about.html")


@app.route("/api/tts/generate", methods=["POST"])
@limiter.limit("10 per minute")
def generate_tts():
    # If verification not setup, handle it first
    if app.config["TURNSTILE_ENABLED"] and not session.get("turnstile_verified"):
        return jsonify({"error": "Turnstile verification required"}), 403

    data = request.json
    text = data.get("text")

    if not text or len(text) > 1000:
        return jsonify({"error": "Invalid or too long text"}), 400

    # Get two random TTS models
    available_models = Model.query.filter_by(
        model_type=ModelType.TTS, is_active=True
    ).all()
    if len(available_models) < 2:
        return jsonify({"error": "Not enough TTS models available"}), 500

    selected_models = random.sample(available_models, 2)

    try:
        # Generate TTS for both models concurrently
        audio_files = []
        model_ids = []

        # Function to process a single model
        def process_model(model):
            # Call TTS service
            audio_path = predict_tts(text, model.id)

            # Copy to temp dir with unique name
            file_uuid = str(uuid.uuid4())
            dest_path = os.path.join(TEMP_AUDIO_DIR, f"{file_uuid}.wav")
            shutil.copy(audio_path, dest_path)

            return {"model_id": model.id, "audio_path": dest_path}

        # Use ThreadPoolExecutor to process models concurrently
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(process_model, selected_models))

        # Extract results
        for result in results:
            model_ids.append(result["model_id"])
            audio_files.append(result["audio_path"])

        # Create session
        session_id = str(uuid.uuid4())
        app.tts_sessions[session_id] = {
            "model_a": model_ids[0],
            "model_b": model_ids[1],
            "audio_a": audio_files[0],
            "audio_b": audio_files[1],
            "text": text,
            "created_at": datetime.utcnow(),
            "expires_at": datetime.utcnow() + timedelta(minutes=30),
            "voted": False,
        }

        # Return audio file paths and session
        return jsonify(
            {
                "session_id": session_id,
                "audio_a": f"/api/tts/audio/{session_id}/a",
                "audio_b": f"/api/tts/audio/{session_id}/b",
                "expires_in": 1800,  # 30 minutes in seconds
            }
        )

    except Exception as e:
        app.logger.error(f"TTS generation error: {str(e)}")
        return jsonify({"error": "Failed to generate TTS"}), 500


@app.route("/api/tts/audio/<session_id>/<model_key>")
def get_audio(session_id, model_key):
    # If verification not setup, handle it first
    if app.config["TURNSTILE_ENABLED"] and not session.get("turnstile_verified"):
        return jsonify({"error": "Turnstile verification required"}), 403

    if session_id not in app.tts_sessions:
        return jsonify({"error": "Invalid or expired session"}), 404

    session_data = app.tts_sessions[session_id]

    # Check if session expired
    if datetime.utcnow() > session_data["expires_at"]:
        cleanup_session(session_id)
        return jsonify({"error": "Session expired"}), 410

    if model_key == "a":
        audio_path = session_data["audio_a"]
    elif model_key == "b":
        audio_path = session_data["audio_b"]
    else:
        return jsonify({"error": "Invalid model key"}), 400

    # Check if file exists
    if not os.path.exists(audio_path):
        return jsonify({"error": "Audio file not found"}), 404

    return send_file(audio_path, mimetype="audio/wav")


@app.route("/api/tts/vote", methods=["POST"])
@limiter.limit("30 per minute")
def submit_vote():
    # If verification not setup, handle it first
    if app.config["TURNSTILE_ENABLED"] and not session.get("turnstile_verified"):
        return jsonify({"error": "Turnstile verification required"}), 403

    data = request.json
    session_id = data.get("session_id")
    chosen_model_key = data.get("chosen_model")  # "a" or "b"

    if not session_id or session_id not in app.tts_sessions:
        return jsonify({"error": "Invalid or expired session"}), 404

    if not chosen_model_key or chosen_model_key not in ["a", "b"]:
        return jsonify({"error": "Invalid chosen model"}), 400

    session_data = app.tts_sessions[session_id]

    # Check if session expired
    if datetime.utcnow() > session_data["expires_at"]:
        cleanup_session(session_id)
        return jsonify({"error": "Session expired"}), 410

    # Check if already voted
    if session_data["voted"]:
        return jsonify({"error": "Vote already submitted for this session"}), 400

    # Get model IDs and audio paths
    chosen_id = (
        session_data["model_a"] if chosen_model_key == "a" else session_data["model_b"]
    )
    rejected_id = (
        session_data["model_b"] if chosen_model_key == "a" else session_data["model_a"]
    )
    chosen_audio_path = (
        session_data["audio_a"] if chosen_model_key == "a" else session_data["audio_b"]
    )
    rejected_audio_path = (
        session_data["audio_b"] if chosen_model_key == "a" else session_data["audio_a"]
    )

    # Record vote in database
    user_id = current_user.id if current_user.is_authenticated else None
    vote, error = record_vote(
        user_id, session_data["text"], chosen_id, rejected_id, ModelType.TTS
    )

    if error:
        return jsonify({"error": error}), 500

    # --- Save preference data ---
    try:
        vote_uuid = str(uuid.uuid4())
        vote_dir = os.path.join("./votes", vote_uuid)
        os.makedirs(vote_dir, exist_ok=True)

        # Copy audio files
        shutil.copy(chosen_audio_path, os.path.join(vote_dir, "chosen.wav"))
        shutil.copy(rejected_audio_path, os.path.join(vote_dir, "rejected.wav"))

        # Create metadata
        chosen_model_obj = Model.query.get(chosen_id)
        rejected_model_obj = Model.query.get(rejected_id)
        metadata = {
            "text": session_data["text"],
            "chosen_model": chosen_model_obj.name if chosen_model_obj else "Unknown",
            "chosen_model_id": chosen_model_obj.id if chosen_model_obj else "Unknown",
            "rejected_model": rejected_model_obj.name if rejected_model_obj else "Unknown",
            "rejected_model_id": rejected_model_obj.id if rejected_model_obj else "Unknown",
            "session_id": session_id,
            "timestamp": datetime.utcnow().isoformat(),
            "username": current_user.username if current_user.is_authenticated else None,
            "model_type": "TTS"
        }
        with open(os.path.join(vote_dir, "metadata.json"), "w") as f:
            json.dump(metadata, f, indent=2)

    except Exception as e:
        app.logger.error(f"Error saving preference data for vote {session_id}: {str(e)}")
        # Continue even if saving preference data fails, vote is already recorded

    # Mark session as voted
    session_data["voted"] = True

    # Return updated models (use previously fetched objects)
    return jsonify(
        {
            "success": True,
            "chosen_model": {"id": chosen_id, "name": chosen_model_obj.name if chosen_model_obj else "Unknown"},
            "rejected_model": {
                "id": rejected_id,
                "name": rejected_model_obj.name if rejected_model_obj else "Unknown",
            },
            "names": {
                "a": (
                    chosen_model_obj.name if chosen_model_key == "a" else rejected_model_obj.name
                    if chosen_model_obj and rejected_model_obj else "Unknown"
                ),
                "b": (
                    rejected_model_obj.name if chosen_model_key == "a" else chosen_model_obj.name
                    if chosen_model_obj and rejected_model_obj else "Unknown"
                ),
            },
        }
    )


def cleanup_session(session_id):
    """Remove session and its audio files"""
    if session_id in app.tts_sessions:
        session = app.tts_sessions[session_id]

        # Remove audio files
        for audio_file in [session["audio_a"], session["audio_b"]]:
            if os.path.exists(audio_file):
                try:
                    os.remove(audio_file)
                except Exception as e:
                    app.logger.error(f"Error removing audio file: {str(e)}")

        # Remove session
        del app.tts_sessions[session_id]


@app.route("/api/conversational/generate", methods=["POST"])
@limiter.limit("5 per minute")
def generate_podcast():
    # If verification not setup, handle it first
    if app.config["TURNSTILE_ENABLED"] and not session.get("turnstile_verified"):
        return jsonify({"error": "Turnstile verification required"}), 403

    data = request.json
    script = data.get("script")

    if not script or not isinstance(script, list) or len(script) < 2:
        return jsonify({"error": "Invalid script format or too short"}), 400

    # Validate script format
    for line in script:
        if not isinstance(line, dict) or "text" not in line or "speaker_id" not in line:
            return (
                jsonify(
                    {
                        "error": "Invalid script line format. Each line must have text and speaker_id"
                    }
                ),
                400,
            )
        if (
            not line["text"]
            or not isinstance(line["speaker_id"], int)
            or line["speaker_id"] not in [0, 1]
        ):
            return (
                jsonify({"error": "Invalid script content. Speaker ID must be 0 or 1"}),
                400,
            )

    # Get two conversational models (currently only CSM and PlayDialog)
    available_models = Model.query.filter_by(
        model_type=ModelType.CONVERSATIONAL, is_active=True
    ).all()

    if len(available_models) < 2:
        return jsonify({"error": "Not enough conversational models available"}), 500

    selected_models = random.sample(available_models, 2)

    try:
        # Generate audio for both models concurrently
        audio_files = []
        model_ids = []

        # Function to process a single model
        def process_model(model):
            # Call conversational TTS service
            audio_content = predict_tts(script, model.id)

            # Save to temp file with unique name
            file_uuid = str(uuid.uuid4())
            dest_path = os.path.join(TEMP_AUDIO_DIR, f"{file_uuid}.wav")

            with open(dest_path, "wb") as f:
                f.write(audio_content)

            return {"model_id": model.id, "audio_path": dest_path}

        # Use ThreadPoolExecutor to process models concurrently
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(process_model, selected_models))

        # Extract results
        for result in results:
            model_ids.append(result["model_id"])
            audio_files.append(result["audio_path"])

        # Create session
        session_id = str(uuid.uuid4())
        script_text = " ".join([line["text"] for line in script])
        app.conversational_sessions[session_id] = {
            "model_a": model_ids[0],
            "model_b": model_ids[1],
            "audio_a": audio_files[0],
            "audio_b": audio_files[1],
            "text": script_text[:1000],  # Limit text length
            "created_at": datetime.utcnow(),
            "expires_at": datetime.utcnow() + timedelta(minutes=30),
            "voted": False,
            "script": script,
        }

        # Return audio file paths and session
        return jsonify(
            {
                "session_id": session_id,
                "audio_a": f"/api/conversational/audio/{session_id}/a",
                "audio_b": f"/api/conversational/audio/{session_id}/b",
                "expires_in": 1800,  # 30 minutes in seconds
            }
        )

    except Exception as e:
        app.logger.error(f"Conversational generation error: {str(e)}")
        return jsonify({"error": f"Failed to generate podcast: {str(e)}"}), 500


@app.route("/api/conversational/audio/<session_id>/<model_key>")
def get_podcast_audio(session_id, model_key):
    # If verification not setup, handle it first
    if app.config["TURNSTILE_ENABLED"] and not session.get("turnstile_verified"):
        return jsonify({"error": "Turnstile verification required"}), 403

    if session_id not in app.conversational_sessions:
        return jsonify({"error": "Invalid or expired session"}), 404

    session_data = app.conversational_sessions[session_id]

    # Check if session expired
    if datetime.utcnow() > session_data["expires_at"]:
        cleanup_conversational_session(session_id)
        return jsonify({"error": "Session expired"}), 410

    if model_key == "a":
        audio_path = session_data["audio_a"]
    elif model_key == "b":
        audio_path = session_data["audio_b"]
    else:
        return jsonify({"error": "Invalid model key"}), 400

    # Check if file exists
    if not os.path.exists(audio_path):
        return jsonify({"error": "Audio file not found"}), 404

    return send_file(audio_path, mimetype="audio/wav")


@app.route("/api/conversational/vote", methods=["POST"])
@limiter.limit("30 per minute")
def submit_podcast_vote():
    # If verification not setup, handle it first
    if app.config["TURNSTILE_ENABLED"] and not session.get("turnstile_verified"):
        return jsonify({"error": "Turnstile verification required"}), 403

    data = request.json
    session_id = data.get("session_id")
    chosen_model_key = data.get("chosen_model")  # "a" or "b"

    if not session_id or session_id not in app.conversational_sessions:
        return jsonify({"error": "Invalid or expired session"}), 404

    if not chosen_model_key or chosen_model_key not in ["a", "b"]:
        return jsonify({"error": "Invalid chosen model"}), 400

    session_data = app.conversational_sessions[session_id]

    # Check if session expired
    if datetime.utcnow() > session_data["expires_at"]:
        cleanup_conversational_session(session_id)
        return jsonify({"error": "Session expired"}), 410

    # Check if already voted
    if session_data["voted"]:
        return jsonify({"error": "Vote already submitted for this session"}), 400

    # Get model IDs and audio paths
    chosen_id = (
        session_data["model_a"] if chosen_model_key == "a" else session_data["model_b"]
    )
    rejected_id = (
        session_data["model_b"] if chosen_model_key == "a" else session_data["model_a"]
    )
    chosen_audio_path = (
        session_data["audio_a"] if chosen_model_key == "a" else session_data["audio_b"]
    )
    rejected_audio_path = (
        session_data["audio_b"] if chosen_model_key == "a" else session_data["audio_a"]
    )

    # Record vote in database
    user_id = current_user.id if current_user.is_authenticated else None
    vote, error = record_vote(
        user_id, session_data["text"], chosen_id, rejected_id, ModelType.CONVERSATIONAL
    )

    if error:
        return jsonify({"error": error}), 500

    # --- Save preference data ---\
    try:
        vote_uuid = str(uuid.uuid4())
        vote_dir = os.path.join("./votes", vote_uuid)
        os.makedirs(vote_dir, exist_ok=True)

        # Copy audio files
        shutil.copy(chosen_audio_path, os.path.join(vote_dir, "chosen.wav"))
        shutil.copy(rejected_audio_path, os.path.join(vote_dir, "rejected.wav"))

        # Create metadata
        chosen_model_obj = Model.query.get(chosen_id)
        rejected_model_obj = Model.query.get(rejected_id)
        metadata = {
            "script": session_data["script"], # Save the full script
            "chosen_model": chosen_model_obj.name if chosen_model_obj else "Unknown",
            "chosen_model_id": chosen_model_obj.id if chosen_model_obj else "Unknown",
            "rejected_model": rejected_model_obj.name if rejected_model_obj else "Unknown",
            "rejected_model_id": rejected_model_obj.id if rejected_model_obj else "Unknown",
            "session_id": session_id,
            "timestamp": datetime.utcnow().isoformat(),
            "username": current_user.username if current_user.is_authenticated else None,
            "model_type": "CONVERSATIONAL"
        }
        with open(os.path.join(vote_dir, "metadata.json"), "w") as f:
            json.dump(metadata, f, indent=2)

    except Exception as e:
        app.logger.error(f"Error saving preference data for conversational vote {session_id}: {str(e)}")
        # Continue even if saving preference data fails, vote is already recorded

    # Mark session as voted
    session_data["voted"] = True

    # Return updated models (use previously fetched objects)
    return jsonify(
        {
            "success": True,
            "chosen_model": {"id": chosen_id, "name": chosen_model_obj.name if chosen_model_obj else "Unknown"},
            "rejected_model": {
                "id": rejected_id,
                "name": rejected_model_obj.name if rejected_model_obj else "Unknown",
            },
            "names": {
                "a": Model.query.get(session_data["model_a"]).name,
                "b": Model.query.get(session_data["model_b"]).name,
            },
        }
    )


def cleanup_conversational_session(session_id):
    """Remove conversational session and its audio files"""
    if session_id in app.conversational_sessions:
        session = app.conversational_sessions[session_id]

        # Remove audio files
        for audio_file in [session["audio_a"], session["audio_b"]]:
            if os.path.exists(audio_file):
                try:
                    os.remove(audio_file)
                except Exception as e:
                    app.logger.error(
                        f"Error removing conversational audio file: {str(e)}"
                    )

        # Remove session
        del app.conversational_sessions[session_id]


# Schedule periodic cleanup
def setup_cleanup():
    def cleanup_expired_sessions():
        with app.app_context(): # Ensure app context for logging
            current_time = datetime.utcnow()
            # Cleanup TTS sessions
            expired_tts_sessions = [
                sid
                for sid, session_data in app.tts_sessions.items()
                if current_time > session_data["expires_at"]
            ]
            for sid in expired_tts_sessions:
                cleanup_session(sid)

            # Cleanup conversational sessions
            expired_conv_sessions = [
                sid
                for sid, session_data in app.conversational_sessions.items()
                if current_time > session_data["expires_at"]
            ]
            for sid in expired_conv_sessions:
                cleanup_conversational_session(sid)
            app.logger.info(f"Cleaned up {len(expired_tts_sessions)} TTS and {len(expired_conv_sessions)} conversational sessions.")


    # Run cleanup every 15 minutes
    scheduler = BackgroundScheduler()
    scheduler.add_job(cleanup_expired_sessions, "interval", minutes=15)
    scheduler.start()
    print("Cleanup scheduler started") # Use print for startup messages


# Schedule periodic tasks (database sync and preference upload)
def setup_periodic_tasks():
    """Setup periodic database synchronization and preference data upload for Spaces"""
    if not IS_SPACES:
        return

    db_path = app.config["SQLALCHEMY_DATABASE_URI"].replace("sqlite:///", "instance/") # Get relative path
    preferences_repo_id = "TTS-AGI/arena-v2-preferences"
    database_repo_id = "TTS-AGI/database-arena-v2"
    votes_dir = "./votes"

    def sync_database():
        """Uploads the database to HF dataset"""
        with app.app_context(): # Ensure app context for logging
            try:
                if not os.path.exists(db_path):
                    app.logger.warning(f"Database file not found at {db_path}, skipping sync.")
                    return

                api = HfApi(token=os.getenv("HF_TOKEN"))
                api.upload_file(
                    path_or_fileobj=db_path,
                    path_in_repo="tts_arena.db",
                    repo_id=database_repo_id,
                    repo_type="dataset",
                )
                app.logger.info(f"Database uploaded to {database_repo_id} at {datetime.utcnow()}")
            except Exception as e:
                app.logger.error(f"Error uploading database to {database_repo_id}: {str(e)}")

    def sync_preferences_data():
        """Zips and uploads preference data folders to HF dataset"""
        with app.app_context(): # Ensure app context for logging
            if not os.path.isdir(votes_dir):
                # app.logger.info(f"Votes directory '{votes_dir}' not found, skipping preference sync.")
                return # Don't log every 5 mins if dir doesn't exist yet

            try:
                api = HfApi(token=os.getenv("HF_TOKEN"))
                vote_uuids = [d for d in os.listdir(votes_dir) if os.path.isdir(os.path.join(votes_dir, d))]

                if not vote_uuids:
                    # app.logger.info("No new preference data to upload.")
                    return # Don't log every 5 mins if no new data

                uploaded_count = 0
                for vote_uuid in vote_uuids:
                    dir_path = os.path.join(votes_dir, vote_uuid)
                    zip_base_path = os.path.join(votes_dir, vote_uuid) # Name zip file same as folder
                    zip_path = f"{zip_base_path}.zip"

                    try:
                        # Create zip archive
                        shutil.make_archive(zip_base_path, 'zip', dir_path)
                        app.logger.info(f"Created zip archive: {zip_path}")

                        # Upload zip file
                        api.upload_file(
                            path_or_fileobj=zip_path,
                            path_in_repo=f"votes/{year}/{month}/{vote_uuid}.zip",
                            repo_id=preferences_repo_id,
                            repo_type="dataset",
                            commit_message=f"Add preference data {vote_uuid}"
                        )
                        app.logger.info(f"Successfully uploaded {zip_path} to {preferences_repo_id}")
                        uploaded_count += 1

                        # Cleanup local files after successful upload
                        try:
                            os.remove(zip_path)
                            shutil.rmtree(dir_path)
                            app.logger.info(f"Cleaned up local files: {zip_path} and {dir_path}")
                        except OSError as e:
                            app.logger.error(f"Error cleaning up files for {vote_uuid}: {str(e)}")

                    except Exception as upload_err:
                        app.logger.error(f"Error processing or uploading preference data for {vote_uuid}: {str(upload_err)}")
                        # Optionally remove zip if it exists but upload failed
                        if os.path.exists(zip_path):
                             try:
                                 os.remove(zip_path)
                             except OSError as e:
                                 app.logger.error(f"Error removing zip file after failed upload {zip_path}: {str(e)}")
                        # Keep the original folder for the next attempt

                if uploaded_count > 0:
                    app.logger.info(f"Finished preference data sync. Uploaded {uploaded_count} new entries.")

            except Exception as e:
                app.logger.error(f"General error during preference data sync: {str(e)}")


    # Schedule periodic tasks
    scheduler = BackgroundScheduler()
    # Sync database less frequently if needed, e.g., every 15 minutes
    scheduler.add_job(sync_database, "interval", minutes=15, id="sync_db_job")
    # Sync preferences more frequently
    scheduler.add_job(sync_preferences_data, "interval", minutes=5, id="sync_pref_job")
    scheduler.start()
    print("Periodic tasks scheduler started (DB sync and Preferences upload)") # Use print for startup


@app.cli.command("init-db")
def init_db():
    """Initialize the database."""
    with app.app_context():
        db.create_all()
        print("Database initialized!")


@app.route("/api/toggle-leaderboard-visibility", methods=["POST"])
def toggle_leaderboard_visibility():
    """Toggle whether the current user appears in the top voters leaderboard"""
    if not current_user.is_authenticated:
        return jsonify({"error": "You must be logged in to change this setting"}), 401
    
    new_status = toggle_user_leaderboard_visibility(current_user.id)
    if new_status is None:
        return jsonify({"error": "User not found"}), 404
        
    return jsonify({
        "success": True, 
        "visible": new_status,
        "message": "You are now visible in the voters leaderboard" if new_status else "You are now hidden from the voters leaderboard"
    })


if __name__ == "__main__":
    with app.app_context():
        # Ensure ./instance and ./votes directories exist
        os.makedirs("instance", exist_ok=True)
        os.makedirs("./votes", exist_ok=True) # Create votes directory if it doesn't exist

        # Download database if it doesn't exist (only on initial space start)
        if IS_SPACES and not os.path.exists(app.config["SQLALCHEMY_DATABASE_URI"].replace("sqlite:///", "")):
             try:
                print("Database not found, downloading from HF dataset...")
                hf_hub_download(
                    repo_id="TTS-AGI/database-arena-v2",
                    filename="tts_arena.db",
                    repo_type="dataset",
                    local_dir="instance", # download to instance/
                    token=os.getenv("HF_TOKEN"),
                )
                print("Database downloaded successfully ✅")
             except Exception as e:
                 print(f"Error downloading database from HF dataset: {str(e)} ⚠️")


        db.create_all()  # Create tables if they don't exist
        insert_initial_models()
        # Setup background tasks
        setup_cleanup()
        setup_periodic_tasks() # Renamed function call

    # Configure Flask to recognize HTTPS when behind a reverse proxy
    from werkzeug.middleware.proxy_fix import ProxyFix

    # Apply ProxyFix middleware to handle reverse proxy headers
    # This ensures Flask generates correct URLs with https scheme
    # X-Forwarded-Proto header will be used to detect the original protocol
    app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

    # Force Flask to prefer HTTPS for generated URLs
    app.config["PREFERRED_URL_SCHEME"] = "https"

    from waitress import serve

    # Configuration for 2 vCPUs:
    # - threads: typically 4-8 threads per CPU core is a good balance
    # - connection_limit: maximum concurrent connections
    # - channel_timeout: prevent hanging connections
    threads = 12  # 6 threads per vCPU is a good balance for mixed IO/CPU workloads

    if IS_SPACES:
        serve(
            app,
            host="0.0.0.0",
            port=int(os.environ.get("PORT", 7860)),
            threads=threads,
            connection_limit=100,
            channel_timeout=30,
            url_scheme='https'
        )
    else:
        print(f"Starting Waitress server with {threads} threads")
        serve(
            app,
            host="0.0.0.0",
            port=5000,
            threads=threads,
            connection_limit=100,
            channel_timeout=30,
            url_scheme='https' # Keep https for local dev if using proxy/tunnel
        )
