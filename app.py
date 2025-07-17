import os
from huggingface_hub import HfApi, hf_hub_download
from apscheduler.schedulers.background import BackgroundScheduler
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import threading # Added for locking
from sqlalchemy import or_ # Added for vote counting query
from datasets import load_dataset

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
from models import (
    hash_sentence, is_sentence_consumed, mark_sentence_consumed,
    get_unconsumed_sentences, get_consumed_sentences_count, get_random_unconsumed_sentence
)
from auth import auth, init_oauth, is_admin
from admin import admin
from security import is_vote_allowed, check_user_security_score, detect_coordinated_voting
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
from langdetect import detect, DetectorFactory

# Set random seed for consistent language detection results
DetectorFactory.seed = 0


def is_english_text(text):
    """
    Detect if the given text is in English.
    Returns True if English, False otherwise.
    """
    try:
        # Remove leading/trailing whitespace and check if text is not empty
        text = text.strip()
        if not text:
            return False
        
        # Detect language
        detected_language = detect(text)
        return detected_language == 'en'
    except Exception:
        # If detection fails, assume it's not English for safety
        return False


def get_client_ip():
    """Get the client's IP address, handling proxies and load balancers."""
    # Check for forwarded headers first (common with reverse proxies)
    if request.headers.get('X-Forwarded-For'):
        # X-Forwarded-For can contain multiple IPs, take the first one
        return request.headers.get('X-Forwarded-For').split(',')[0].strip()
    elif request.headers.get('X-Real-IP'):
        return request.headers.get('X-Real-IP')
    elif request.headers.get('CF-Connecting-IP'):  # Cloudflare
        return request.headers.get('CF-Connecting-IP')
    else:
        return request.remote_addr


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
    default_limits=["2000 per day", "50 per minute"],
    storage_uri="memory://",
)

# TTS Cache Configuration - Read from environment
TTS_CACHE_SIZE = int(os.getenv("TTS_CACHE_SIZE", "10"))
CACHE_AUDIO_SUBDIR = "cache"
tts_cache = {} # sentence -> {model_a, model_b, audio_a, audio_b, created_at}
tts_cache_lock = threading.Lock()
SMOOTHING_FACTOR_MODEL_SELECTION = 500 # For weighted random model selection
# Increased max_workers to 8 for concurrent generation/refill
cache_executor = ThreadPoolExecutor(max_workers=8, thread_name_prefix='CacheReplacer')
all_harvard_sentences = [] # Keep the full list available

# Create temp directories
TEMP_AUDIO_DIR = os.path.join(tempfile.gettempdir(), "tts_arena_audio")
CACHE_AUDIO_DIR = os.path.join(TEMP_AUDIO_DIR, CACHE_AUDIO_SUBDIR)
os.makedirs(TEMP_AUDIO_DIR, exist_ok=True)
os.makedirs(CACHE_AUDIO_DIR, exist_ok=True) # Ensure cache subdir exists


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

# Load sentences from the TTS-AGI/arena-prompts dataset
print("Loading TTS-AGI/arena-prompts dataset...")
dataset = load_dataset("TTS-AGI/arena-prompts", split="train")
# Extract the text column and clean up
all_harvard_sentences = [item['text'].strip() for item in dataset if item['text'] and item['text'].strip()]
print(f"Loaded {len(all_harvard_sentences)} sentences from dataset")

# Initialize initial_sentences as empty - will be populated with unconsumed sentences only
initial_sentences = []

@app.route("/")
def arena():
    # Pass a subset of sentences for the random button fallback
    return render_template("arena.html", harvard_sentences=json.dumps(initial_sentences))


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


# --- TTS Caching Functions ---

def generate_and_save_tts(text, model_id, output_dir):
    """Generates TTS and saves it to a specific directory, returning the full path."""
    temp_audio_path = None # Initialize to None
    try:
        app.logger.debug(f"[TTS Gen {model_id}] Starting generation for: '{text[:30]}...'")
        # If predict_tts saves file itself and returns path:
        temp_audio_path = predict_tts(text, model_id)
        app.logger.debug(f"[TTS Gen {model_id}] predict_tts returned: {temp_audio_path}")

        if not temp_audio_path or not os.path.exists(temp_audio_path):
            app.logger.warning(f"[TTS Gen {model_id}] predict_tts failed or returned invalid path: {temp_audio_path}")
            raise ValueError("predict_tts did not return a valid path or file does not exist")

        file_uuid = str(uuid.uuid4())
        dest_path = os.path.join(output_dir, f"{file_uuid}.wav")
        app.logger.debug(f"[TTS Gen {model_id}] Moving {temp_audio_path} to {dest_path}")
        # Move the file generated by predict_tts to the target cache directory
        shutil.move(temp_audio_path, dest_path)
        app.logger.debug(f"[TTS Gen {model_id}] Move successful. Returning {dest_path}")
        return dest_path

    except Exception as e:
        app.logger.error(f"Error generating/saving TTS for model {model_id} and text '{text[:30]}...': {str(e)}")
        # Ensure temporary file from predict_tts (if any) is cleaned up
        if temp_audio_path and os.path.exists(temp_audio_path):
            try:
                app.logger.debug(f"[TTS Gen {model_id}] Cleaning up temporary file {temp_audio_path} after error.")
                os.remove(temp_audio_path)
            except OSError:
                pass # Ignore error if file couldn't be removed
        return None


def _generate_cache_entry_task(sentence):
    """Task function to generate audio for a sentence and add to cache."""
    # Wrap the entire task in an application context
    with app.app_context():
        if not sentence:
            # Select a new sentence if not provided (for replacement)
            with tts_cache_lock:
                cached_keys = set(tts_cache.keys())
            # Get unconsumed sentences that are also not already cached
            unconsumed_sentences = get_unconsumed_sentences(all_harvard_sentences)
            available_sentences = [s for s in unconsumed_sentences if s not in cached_keys]
            if not available_sentences:
                app.logger.warning("No more unconsumed sentences available for caching. All sentences have been consumed.")
                return
            sentence = random.choice(available_sentences)

        # app.logger.info removed duplicate log
        print(f"[Cache Task] Querying models for: '{sentence[:50]}...'")
        available_models = Model.query.filter_by(
            model_type=ModelType.TTS, is_active=True
        ).all()

        if len(available_models) < 2:
            app.logger.error("Not enough active TTS models to generate cache entry.")
            return

        try:
            models = get_weighted_random_models(available_models, 2, ModelType.TTS)
            model_a_id = models[0].id
            model_b_id = models[1].id

            # Generate audio concurrently using a local executor for clarity within the task
            with ThreadPoolExecutor(max_workers=2, thread_name_prefix='AudioGen') as audio_executor:
                future_a = audio_executor.submit(generate_and_save_tts, sentence, model_a_id, CACHE_AUDIO_DIR)
                future_b = audio_executor.submit(generate_and_save_tts, sentence, model_b_id, CACHE_AUDIO_DIR)

                timeout_seconds = 120
                audio_a_path = future_a.result(timeout=timeout_seconds)
                audio_b_path = future_b.result(timeout=timeout_seconds)

            if audio_a_path and audio_b_path:
                with tts_cache_lock:
                    # Only add if the sentence isn't already back in the cache
                    # And ensure cache size doesn't exceed limit
                    if sentence not in tts_cache and len(tts_cache) < TTS_CACHE_SIZE:
                        tts_cache[sentence] = {
                            "model_a": model_a_id,
                            "model_b": model_b_id,
                            "audio_a": audio_a_path,
                            "audio_b": audio_b_path,
                            "created_at": datetime.utcnow(),
                        }
                        # Mark sentence as consumed for cache usage
                        mark_sentence_consumed(sentence, usage_type='cache')
                        app.logger.info(f"Successfully cached entry for: '{sentence[:50]}...'")
                    elif sentence in tts_cache:
                         app.logger.warning(f"Sentence '{sentence[:50]}...' already re-cached. Discarding new generation.")
                         # Clean up the newly generated files if not added
                         if os.path.exists(audio_a_path): os.remove(audio_a_path)
                         if os.path.exists(audio_b_path): os.remove(audio_b_path)
                    else: # Cache is full
                        app.logger.warning(f"Cache is full ({len(tts_cache)} entries). Discarding new generation for '{sentence[:50]}...'.")
                        # Clean up the newly generated files if not added
                        if os.path.exists(audio_a_path): os.remove(audio_a_path)
                        if os.path.exists(audio_b_path): os.remove(audio_b_path)

            else:
                app.logger.error(f"Failed to generate one or both audio files for cache: '{sentence[:50]}...'")
                # Clean up whichever file might have been created
                if audio_a_path and os.path.exists(audio_a_path): os.remove(audio_a_path)
                if audio_b_path and os.path.exists(audio_b_path): os.remove(audio_b_path)

        except Exception as e:
            # Log the exception within the app context
            app.logger.error(f"Exception in _generate_cache_entry_task for '{sentence[:50]}...': {str(e)}", exc_info=True)


def update_initial_sentences():
    """Update initial sentences to only include unconsumed ones."""
    global initial_sentences
    try:
        unconsumed_for_initial = get_unconsumed_sentences(all_harvard_sentences)
        if unconsumed_for_initial:
            initial_sentences = random.sample(unconsumed_for_initial, min(len(unconsumed_for_initial), 500))
            print(f"Updated initial sentences with {len(initial_sentences)} unconsumed sentences")
        else:
            print("Warning: No unconsumed sentences available for initial selection, disabling fallback")
            initial_sentences = []  # No fallback to consumed sentences
    except Exception as e:
        print(f"Error updating initial sentences: {e}, disabling fallback for security")
        initial_sentences = []  # No fallback to consumed sentences


def initialize_tts_cache():
    print("Initializing TTS cache")
    """Selects initial sentences and starts generation tasks."""
    with app.app_context(): # Ensure access to models
        if not all_harvard_sentences:
            app.logger.error("Harvard sentences not loaded. Cannot initialize cache.")
            return

        # Update initial sentences with unconsumed ones
        update_initial_sentences()

        # Only use unconsumed sentences for initial cache population
        unconsumed_sentences = get_unconsumed_sentences(all_harvard_sentences)
        if not unconsumed_sentences:
            app.logger.error("No unconsumed sentences available for cache initialization. Cache will remain empty.")
            app.logger.warning("WARNING: All sentences from the dataset have been consumed. No new TTS generations will be possible.")
            return
        initial_selection = random.sample(unconsumed_sentences, min(len(unconsumed_sentences), TTS_CACHE_SIZE))
        app.logger.info(f"Initializing TTS cache with {len(initial_selection)} sentences...")

        for sentence in initial_selection:
            # Use the main cache_executor for initial population too
            cache_executor.submit(_generate_cache_entry_task, sentence)
        app.logger.info("Submitted initial cache generation tasks.")

# --- End TTS Caching Functions ---


@app.route("/api/tts/generate", methods=["POST"])
@limiter.limit("10 per minute") # Keep limit, cached responses are still requests
def generate_tts():
    # If verification not setup, handle it first
    if app.config["TURNSTILE_ENABLED"] and not session.get("turnstile_verified"):
        return jsonify({"error": "Turnstile verification required"}), 403

    # Require user to be logged in to generate audio
    if not current_user.is_authenticated:
        return jsonify({"error": "You must be logged in to generate audio"}), 401

    data = request.json
    text = data.get("text", "").strip() # Ensure text is stripped

    if not text or len(text) > 1000:
        return jsonify({"error": "Invalid or too long text"}), 400
    
    # Check if text is in English
    if not is_english_text(text):
        return jsonify({"error": "Only English language text is supported for now. Please provide text in English. A multilingual Arena is coming soon!"}), 400
    
    # Check if sentence has already been consumed
    if is_sentence_consumed(text):
        remaining_count = len(get_unconsumed_sentences(all_harvard_sentences))
        if remaining_count == 0:
            return jsonify({"error": "This sentence has already been used and no unconsumed sentences remain. All sentences from the dataset have been consumed."}), 400
        else:
            return jsonify({"error": f"This sentence has already been used. Please select a different sentence. {remaining_count} sentences remain available."}), 400

    # --- Cache Check ---
    cache_hit = False
    session_data_from_cache = None
    with tts_cache_lock:
        if text in tts_cache:
            cache_hit = True
            cached_entry = tts_cache.pop(text) # Remove from cache immediately
            app.logger.info(f"TTS Cache HIT for: '{text[:50]}...'")

            # Prepare session data using cached info
            session_id = str(uuid.uuid4())
            session_data_from_cache = {
                "model_a": cached_entry["model_a"],
                "model_b": cached_entry["model_b"],
                "audio_a": cached_entry["audio_a"], # Paths are now from cache_dir
                "audio_b": cached_entry["audio_b"],
                "text": text,
                "created_at": datetime.utcnow(),
                "expires_at": datetime.utcnow() + timedelta(minutes=30),
                "voted": False,
                "cache_hit": True,
            }
            app.tts_sessions[session_id] = session_data_from_cache
            
            # Note: Sentence was already marked as consumed when it was cached
            # No need to mark it again here

            # --- Trigger background tasks to refill the cache ---
            # Calculate how many slots need refilling
            current_cache_size = len(tts_cache) # Size *before* adding potentially new items
            needed_refills = TTS_CACHE_SIZE - current_cache_size
            # Limit concurrent refills to 8 or the actual need
            refills_to_submit = min(needed_refills, 8)

            if refills_to_submit > 0:
                app.logger.info(f"Cache hit: Submitting {refills_to_submit} background task(s) to refill cache (current size: {current_cache_size}, target: {TTS_CACHE_SIZE}).")
                for _ in range(refills_to_submit):
                     # Pass None to signal replacement selection within the task
                    cache_executor.submit(_generate_cache_entry_task, None)
            else:
                app.logger.info(f"Cache hit: Cache is already full or at target size ({current_cache_size}/{TTS_CACHE_SIZE}). No refill tasks submitted.")
            # --- End Refill Trigger ---

    if cache_hit and session_data_from_cache:
        # Return response using cached data
        # Note: The files are now managed by the session lifecycle (cleanup_session)
        return jsonify(
            {
                "session_id": session_id,
                "audio_a": f"/api/tts/audio/{session_id}/a",
                "audio_b": f"/api/tts/audio/{session_id}/b",
                "expires_in": 1800,  # 30 minutes in seconds
                "cache_hit": True,
            }
        )
    # --- End Cache Check ---

    # --- Cache Miss: Generate on the fly ---
    app.logger.info(f"TTS Cache MISS for: '{text[:50]}...'. Generating on the fly.")
    available_models = Model.query.filter_by(
        model_type=ModelType.TTS, is_active=True
    ).all()
    if len(available_models) < 2:
        return jsonify({"error": "Not enough TTS models available"}), 500

    selected_models = get_weighted_random_models(available_models, 2, ModelType.TTS)

    try:
        audio_files = []
        model_ids = []

        # Function to process a single model (generate directly to TEMP_AUDIO_DIR, not cache subdir)
        def process_model_on_the_fly(model):
             # Generate and save directly to the main temp dir
             # Assume predict_tts handles saving temporary files
             temp_audio_path = predict_tts(text, model.id)
             if not temp_audio_path or not os.path.exists(temp_audio_path):
                 raise ValueError(f"predict_tts failed for model {model.id}")

             # Create a unique name in the main TEMP_AUDIO_DIR for the session
             file_uuid = str(uuid.uuid4())
             dest_path = os.path.join(TEMP_AUDIO_DIR, f"{file_uuid}.wav")
             shutil.move(temp_audio_path, dest_path) # Move from predict_tts's temp location

             return {"model_id": model.id, "audio_path": dest_path}


        # Use ThreadPoolExecutor to process models concurrently
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(process_model_on_the_fly, selected_models))

        # Extract results
        for result in results:
            model_ids.append(result["model_id"])
            audio_files.append(result["audio_path"])

        # Create session
        session_id = str(uuid.uuid4())
        app.tts_sessions[session_id] = {
            "model_a": model_ids[0],
            "model_b": model_ids[1],
            "audio_a": audio_files[0], # Paths are now from TEMP_AUDIO_DIR directly
            "audio_b": audio_files[1],
            "text": text,
            "created_at": datetime.utcnow(),
            "expires_at": datetime.utcnow() + timedelta(minutes=30),
            "voted": False,
            "cache_hit": False,
        }
        
        # Don't mark as consumed yet - wait until vote is submitted to maintain security
        # while allowing legitimate votes to count for ELO

        # Return audio file paths and session
        return jsonify(
            {
                "session_id": session_id,
                "audio_a": f"/api/tts/audio/{session_id}/a",
                "audio_b": f"/api/tts/audio/{session_id}/b",
                "expires_in": 1800,
                "cache_hit": False,
            }
        )

    except Exception as e:
        app.logger.error(f"TTS on-the-fly generation error: {str(e)}", exc_info=True)
        # Cleanup any files potentially created during the failed attempt
        if 'results' in locals():
             for res in results:
                 if 'audio_path' in res and os.path.exists(res['audio_path']):
                     try:
                         os.remove(res['audio_path'])
                     except OSError:
                         pass
        return jsonify({"error": "Failed to generate TTS"}), 500
    # --- End Cache Miss ---


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

    # Require user to be logged in to vote
    if not current_user.is_authenticated:
        return jsonify({"error": "You must be logged in to vote"}), 401

    # Security checks for vote manipulation prevention
    client_ip = get_client_ip()
    vote_allowed, security_reason, security_score = is_vote_allowed(current_user.id, client_ip)
    
    if not vote_allowed:
        app.logger.warning(f"Vote blocked for user {current_user.username} (ID: {current_user.id}): {security_reason} (Score: {security_score})")
        return jsonify({"error": f"Vote not allowed: {security_reason}"}), 403

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

    # Calculate session duration and gather analytics data
    vote_time = datetime.utcnow()
    session_duration = (vote_time - session_data["created_at"]).total_seconds()
    client_ip = get_client_ip()
    user_agent = request.headers.get('User-Agent')
    cache_hit = session_data.get("cache_hit", False)

    # Record vote in database with analytics data
    vote, error = record_vote(
        current_user.id, 
        session_data["text"], 
        chosen_id, 
        rejected_id, 
        ModelType.TTS,
        session_duration=session_duration,
        ip_address=client_ip,
        user_agent=user_agent,
        generation_date=session_data["created_at"],
        cache_hit=cache_hit,
        all_dataset_sentences=all_harvard_sentences
    )

    if error:
        return jsonify({"error": error}), 500

    # Sentence consumption is now handled within record_vote function

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
            "username": current_user.username,
            "model_type": "TTS"
        }
        with open(os.path.join(vote_dir, "metadata.json"), "w") as f:
            json.dump(metadata, f, indent=2)

    except Exception as e:
        app.logger.error(f"Error saving preference data for vote {session_id}: {str(e)}")
        # Continue even if saving preference data fails, vote is already recorded

    # Mark session as voted
    session_data["voted"] = True

    # Check for coordinated voting campaigns (async to not slow down response)
    try:
        from threading import Thread
        campaign_check_thread = Thread(target=check_for_coordinated_campaigns)
        campaign_check_thread.daemon = True
        campaign_check_thread.start()
    except Exception as e:
        app.logger.error(f"Error starting coordinated campaign check thread: {str(e)}")

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

    # Require user to be logged in to generate audio
    if not current_user.is_authenticated:
        return jsonify({"error": "You must be logged in to generate audio"}), 401

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

    selected_models = get_weighted_random_models(available_models, 2, ModelType.CONVERSATIONAL)

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
            "cache_hit": False,  # Conversational is always generated on-demand
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

    # Require user to be logged in to vote
    if not current_user.is_authenticated:
        return jsonify({"error": "You must be logged in to vote"}), 401

    # Security checks for vote manipulation prevention
    client_ip = get_client_ip()
    vote_allowed, security_reason, security_score = is_vote_allowed(current_user.id, client_ip)
    
    if not vote_allowed:
        app.logger.warning(f"Conversational vote blocked for user {current_user.username} (ID: {current_user.id}): {security_reason} (Score: {security_score})")
        return jsonify({"error": f"Vote not allowed: {security_reason}"}), 403

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

    # Calculate session duration and gather analytics data
    vote_time = datetime.utcnow()
    session_duration = (vote_time - session_data["created_at"]).total_seconds()
    client_ip = get_client_ip()
    user_agent = request.headers.get('User-Agent')
    cache_hit = session_data.get("cache_hit", False)

    # Record vote in database with analytics data
    vote, error = record_vote(
        current_user.id, 
        session_data["text"], 
        chosen_id, 
        rejected_id, 
        ModelType.CONVERSATIONAL,
        session_duration=session_duration,
        ip_address=client_ip,
        user_agent=user_agent,
        generation_date=session_data["created_at"],
        cache_hit=cache_hit,
        all_dataset_sentences=all_harvard_sentences  # Note: conversational uses scripts, not sentences
    )

    if error:
        return jsonify({"error": error}), 500

    # Sentence consumption is now handled within record_vote function

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
            "username": current_user.username,
            "model_type": "CONVERSATIONAL"
        }
        with open(os.path.join(vote_dir, "metadata.json"), "w") as f:
            json.dump(metadata, f, indent=2)

    except Exception as e:
        app.logger.error(f"Error saving preference data for conversational vote {session_id}: {str(e)}")
        # Continue even if saving preference data fails, vote is already recorded

    # Mark session as voted
    session_data["voted"] = True

    # Check for coordinated voting campaigns (async to not slow down response)
    try:
        from threading import Thread
        campaign_check_thread = Thread(target=check_for_coordinated_campaigns)
        campaign_check_thread.daemon = True
        campaign_check_thread.start()
    except Exception as e:
        app.logger.error(f"Error starting coordinated campaign check thread: {str(e)}")

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

    # Also cleanup potentially expired cache entries (e.g., > 1 hour old)
    # This prevents stale cache entries if generation is slow or failing
    # cleanup_stale_cache_entries()

    # Run cleanup every 15 minutes
    scheduler = BackgroundScheduler(daemon=True) # Run scheduler as daemon thread
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
        """Zips and uploads preference data folders in batches to HF dataset"""
        with app.app_context(): # Ensure app context for logging
            if not os.path.isdir(votes_dir):
                return # Don't log every 5 mins if dir doesn't exist yet

            temp_batch_dir = None # Initialize to manage cleanup
            temp_individual_zip_dir = None # Initialize for individual zips
            local_batch_zip_path = None # Initialize for batch zip path

            try:
                api = HfApi(token=os.getenv("HF_TOKEN"))
                vote_uuids = [d for d in os.listdir(votes_dir) if os.path.isdir(os.path.join(votes_dir, d))]

                if not vote_uuids:
                    return # No data to process

                app.logger.info(f"Found {len(vote_uuids)} vote directories to process.")

                # Create temporary directories
                temp_batch_dir = tempfile.mkdtemp(prefix="hf_batch_")
                temp_individual_zip_dir = tempfile.mkdtemp(prefix="hf_indiv_zips_")
                app.logger.debug(f"Created temp directories: {temp_batch_dir}, {temp_individual_zip_dir}")

                processed_vote_dirs = []
                individual_zips_in_batch = []

                # 1. Create individual zips and move them to the batch directory
                for vote_uuid in vote_uuids:
                    dir_path = os.path.join(votes_dir, vote_uuid)
                    individual_zip_base_path = os.path.join(temp_individual_zip_dir, vote_uuid)
                    individual_zip_path = f"{individual_zip_base_path}.zip"

                    try:
                        shutil.make_archive(individual_zip_base_path, 'zip', dir_path)
                        app.logger.debug(f"Created individual zip: {individual_zip_path}")

                        # Move the created zip into the batch directory
                        final_individual_zip_path = os.path.join(temp_batch_dir, f"{vote_uuid}.zip")
                        shutil.move(individual_zip_path, final_individual_zip_path)
                        app.logger.debug(f"Moved individual zip to batch dir: {final_individual_zip_path}")

                        processed_vote_dirs.append(dir_path) # Mark original dir for later cleanup
                        individual_zips_in_batch.append(final_individual_zip_path)

                    except Exception as zip_err:
                        app.logger.error(f"Error creating or moving zip for {vote_uuid}: {str(zip_err)}")
                        # Clean up partial zip if it exists
                        if os.path.exists(individual_zip_path):
                            try:
                                os.remove(individual_zip_path)
                            except OSError:
                                pass
                        # Continue processing other votes

                # Clean up the temporary dir used for creating individual zips
                shutil.rmtree(temp_individual_zip_dir)
                temp_individual_zip_dir = None # Mark as cleaned
                app.logger.debug("Cleaned up temporary individual zip directory.")

                if not individual_zips_in_batch:
                    app.logger.warning("No individual zips were successfully created for batching.")
                    # Clean up batch dir if it's empty or only contains failed attempts
                    if temp_batch_dir and os.path.exists(temp_batch_dir):
                         shutil.rmtree(temp_batch_dir)
                         temp_batch_dir = None
                    return

                # 2. Create the batch zip file
                batch_timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
                batch_uuid_short = str(uuid.uuid4())[:8]
                batch_zip_filename = f"{batch_timestamp}_batch_{batch_uuid_short}.zip"
                # Create batch zip in a standard temp location first
                local_batch_zip_base = os.path.join(tempfile.gettempdir(), batch_zip_filename.replace('.zip', ''))
                local_batch_zip_path = f"{local_batch_zip_base}.zip"

                app.logger.info(f"Creating batch zip: {local_batch_zip_path} with {len(individual_zips_in_batch)} individual zips.")
                shutil.make_archive(local_batch_zip_base, 'zip', temp_batch_dir)
                app.logger.info(f"Batch zip created successfully: {local_batch_zip_path}")

                # 3. Upload the batch zip file
                hf_repo_path = f"votes/{year}/{month}/{batch_zip_filename}"
                app.logger.info(f"Uploading batch zip to HF Hub: {preferences_repo_id}/{hf_repo_path}")

                api.upload_file(
                    path_or_fileobj=local_batch_zip_path,
                    path_in_repo=hf_repo_path,
                    repo_id=preferences_repo_id,
                    repo_type="dataset",
                    commit_message=f"Add batch preference data {batch_zip_filename} ({len(individual_zips_in_batch)} votes)"
                )
                app.logger.info(f"Successfully uploaded batch {batch_zip_filename} to {preferences_repo_id}")

                # 4. Cleanup after successful upload
                app.logger.info("Cleaning up local files after successful upload.")
                # Remove original vote directories that were successfully zipped and uploaded
                for dir_path in processed_vote_dirs:
                    try:
                        shutil.rmtree(dir_path)
                        app.logger.debug(f"Removed original vote directory: {dir_path}")
                    except OSError as e:
                        app.logger.error(f"Error removing processed vote directory {dir_path}: {str(e)}")

                # Remove the temporary batch directory (containing the individual zips)
                shutil.rmtree(temp_batch_dir)
                temp_batch_dir = None
                app.logger.debug("Removed temporary batch directory.")

                # Remove the local batch zip file
                os.remove(local_batch_zip_path)
                local_batch_zip_path = None
                app.logger.debug("Removed local batch zip file.")

                app.logger.info(f"Finished preference data sync. Uploaded batch {batch_zip_filename}.")

            except Exception as e:
                app.logger.error(f"Error during preference data batch sync: {str(e)}", exc_info=True)
                # If upload failed, the local batch zip might exist, clean it up.
                if local_batch_zip_path and os.path.exists(local_batch_zip_path):
                    try:
                        os.remove(local_batch_zip_path)
                        app.logger.debug("Cleaned up local batch zip after failed upload.")
                    except OSError as clean_err:
                        app.logger.error(f"Error cleaning up batch zip after failed upload: {clean_err}")
                # Do NOT remove temp_batch_dir if it exists; its contents will be retried next time.
                # Do NOT remove original vote directories if upload failed.

            finally:
                # Final cleanup for temporary directories in case of unexpected exits
                if temp_individual_zip_dir and os.path.exists(temp_individual_zip_dir):
                    try:
                        shutil.rmtree(temp_individual_zip_dir)
                    except Exception as final_clean_err:
                        app.logger.error(f"Error in final cleanup (indiv zips): {final_clean_err}")
                # Only clean up batch dir in finally block if it *wasn't* kept intentionally after upload failure
                if temp_batch_dir and os.path.exists(temp_batch_dir):
                     # Check if an upload attempt happened and failed
                     upload_failed = 'e' in locals() and isinstance(e, Exception) # Crude check if exception occurred
                     if not upload_failed: # If no upload error or upload succeeded, clean up
                        try:
                            shutil.rmtree(temp_batch_dir)
                        except Exception as final_clean_err:
                            app.logger.error(f"Error in final cleanup (batch dir): {final_clean_err}")
                     else:
                         app.logger.warning("Keeping temporary batch directory due to upload failure for next attempt.")


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


@app.route("/api/tts/cached-sentences")
def get_cached_sentences():
    """Returns a list of unconsumed sentences available for random selection."""
    # Get unconsumed sentences from the full pool (not just cached ones)
    unconsumed_sentences = get_unconsumed_sentences(all_harvard_sentences)
    
    # Limit the response size to avoid overwhelming the frontend
    max_sentences = 1000
    if len(unconsumed_sentences) > max_sentences:
        import random
        unconsumed_sentences = random.sample(unconsumed_sentences, max_sentences)
    
    return jsonify(unconsumed_sentences)


@app.route("/api/tts/sentence-stats")
def get_sentence_stats():
    """Returns statistics about sentence consumption."""
    total_sentences = len(all_harvard_sentences)
    consumed_count = get_consumed_sentences_count()
    remaining_count = total_sentences - consumed_count
    
    return jsonify({
        "total_sentences": total_sentences,
        "consumed_sentences": consumed_count,
        "remaining_sentences": remaining_count,
        "consumption_percentage": round((consumed_count / total_sentences) * 100, 2) if total_sentences > 0 else 0
    })


@app.route("/api/tts/random-sentence")
def get_random_sentence():
    """Returns a random unconsumed sentence."""
    random_sentence = get_random_unconsumed_sentence(all_harvard_sentences)
    if random_sentence:
        return jsonify({"sentence": random_sentence})
    else:
        total_sentences = len(all_harvard_sentences)
        consumed_count = get_consumed_sentences_count()
        return jsonify({
            "error": "No unconsumed sentences available", 
            "details": f"All {total_sentences} sentences have been consumed ({consumed_count} total consumed)"
        }), 404


def get_weighted_random_models(
    applicable_models: list[Model], num_to_select: int, model_type: ModelType
) -> list[Model]:
    """
    Selects a specified number of models randomly from a list of applicable_models,
    weighting models with fewer votes higher. A smoothing factor is used to ensure
    the preference is slight and to prevent models with zero votes from being
    overwhelmingly favored. Models are selected without replacement.

    Assumes len(applicable_models) >= num_to_select, which should be checked by the caller.
    """
    model_votes_counts = {}
    for model in applicable_models:
        votes = (
            Vote.query.filter(Vote.model_type == model_type)
            .filter(or_(Vote.model_chosen == model.id, Vote.model_rejected == model.id))
            .count()
        )
        model_votes_counts[model.id] = votes

    weights = [
        1.0 / (model_votes_counts[model.id] + SMOOTHING_FACTOR_MODEL_SELECTION)
        for model in applicable_models
    ]

    selected_models_list = []
    # Create copies to modify during selection process
    current_candidates = list(applicable_models)
    current_weights = list(weights)

    # Assumes num_to_select is positive and less than or equal to len(current_candidates)
    # Callers should ensure this (e.g., len(available_models) >= 2).
    for _ in range(num_to_select):
        if not current_candidates: # Safety break
            app.logger.warning("Not enough candidates left for weighted selection.")
            break
        
        chosen_model = random.choices(current_candidates, weights=current_weights, k=1)[0]
        selected_models_list.append(chosen_model)

        try:
            idx_to_remove = current_candidates.index(chosen_model)
            current_candidates.pop(idx_to_remove)
            current_weights.pop(idx_to_remove)
        except ValueError:
            # This should ideally not happen if chosen_model came from current_candidates.
            app.logger.error(f"Error removing model {chosen_model.id} from weighted selection candidates.")
            break # Avoid potential issues

    return selected_models_list


def check_for_coordinated_campaigns():
    """Check all active models for potential coordinated voting campaigns"""
    try:
        from security import detect_coordinated_voting
        from models import Model, ModelType
        
        # Check TTS models
        tts_models = Model.query.filter_by(model_type=ModelType.TTS, is_active=True).all()
        for model in tts_models:
            try:
                detect_coordinated_voting(model.id)
            except Exception as e:
                app.logger.error(f"Error checking coordinated voting for TTS model {model.id}: {str(e)}")
        
        # Check conversational models
        conv_models = Model.query.filter_by(model_type=ModelType.CONVERSATIONAL, is_active=True).all()
        for model in conv_models:
            try:
                detect_coordinated_voting(model.id)
            except Exception as e:
                app.logger.error(f"Error checking coordinated voting for conversational model {model.id}: {str(e)}")
                
    except Exception as e:
        app.logger.error(f"Error in coordinated campaign check: {str(e)}")


if __name__ == "__main__":
    with app.app_context():
        # Ensure ./instance and ./votes directories exist
        os.makedirs("instance", exist_ok=True)
        os.makedirs("./votes", exist_ok=True) # Create votes directory if it doesn't exist
        os.makedirs(CACHE_AUDIO_DIR, exist_ok=True) # Ensure cache audio dir exists

        # Clean up old cache audio files on startup
        try:
            app.logger.info(f"Clearing old cache audio files from {CACHE_AUDIO_DIR}")
            for filename in os.listdir(CACHE_AUDIO_DIR):
                file_path = os.path.join(CACHE_AUDIO_DIR, filename)
                try:
                    if os.path.isfile(file_path) or os.path.islink(file_path):
                        os.unlink(file_path)
                    elif os.path.isdir(file_path):
                        shutil.rmtree(file_path)
                except Exception as e:
                    app.logger.error(f'Failed to delete {file_path}. Reason: {e}')
        except Exception as e:
             app.logger.error(f"Error clearing cache directory {CACHE_AUDIO_DIR}: {e}")


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
        initialize_tts_cache() # Start populating the cache
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
