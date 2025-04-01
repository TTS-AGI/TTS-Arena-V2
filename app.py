from flask import Flask, render_template, g, request, jsonify, send_file
from flask_login import LoginManager, current_user
from models import *
from auth import auth, init_oauth
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

# Check if running in a Huggin Face Space
IS_SPACES = False
if os.getenv("SPACE_REPO_NAME"):
    print("Running in a Hugging Face Space")
    IS_SPACES = True

# Load environment variables
if not IS_SPACES:
    load_dotenv() # Only load .env if not running in a Hugging Face Space

app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", os.urandom(24))
app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv(
    "DATABASE_URI", "sqlite:///tts_arena.db"
)
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SESSION_COOKIE_SECURE"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "None" if IS_SPACES else "Lax" # HF Spaces uses iframes to load the app, so we need to set SAMESITE to None
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=30)  # Set to desired duration

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
tts_sessions = {}

# Register blueprints
app.register_blueprint(auth, url_prefix="/auth")


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


@app.before_request
def before_request():
    g.user = current_user


@app.route("/")
def arena():
    return render_template("arena.html")


@app.route("/leaderboard")
def leaderboard():
    tts_leaderboard = get_leaderboard_data(ModelType.TTS)
    conversational_leaderboard = get_leaderboard_data(ModelType.CONVERSATIONAL)
    
    # Initialize personal leaderboard data
    tts_personal_leaderboard = None
    conversational_personal_leaderboard = None
    
    # If user is logged in, get their personal leaderboard
    if current_user.is_authenticated:
        tts_personal_leaderboard = get_user_leaderboard(current_user.id, ModelType.TTS)
        conversational_personal_leaderboard = get_user_leaderboard(current_user.id, ModelType.CONVERSATIONAL)
    
    return render_template("leaderboard.html", 
                           tts_leaderboard=tts_leaderboard,
                           conversational_leaderboard=conversational_leaderboard,
                           tts_personal_leaderboard=tts_personal_leaderboard,
                           conversational_personal_leaderboard=conversational_personal_leaderboard)


@app.route("/about")
def about():
    return render_template("about.html")


@app.route("/api/tts/generate", methods=["POST"])
@limiter.limit("10 per minute")
def generate_tts():
    data = request.json
    text = data.get("text")
    
    if not text or len(text) > 1000:
        return jsonify({"error": "Invalid or too long text"}), 400
    
    # Get two random TTS models
    available_models = Model.query.filter_by(model_type=ModelType.TTS, is_active=True).all()
    if len(available_models) < 2:
        return jsonify({"error": "Not enough TTS models available"}), 500
    
    selected_models = random.sample(available_models, 2)
    
    try:
        # Generate TTS for both models
        audio_files = []
        model_ids = []
        
        for model in selected_models:
            # Call TTS service
            audio_path = predict_tts(text, model.id)
            
            # Copy to temp dir with unique name
            file_uuid = str(uuid.uuid4())
            dest_path = os.path.join(TEMP_AUDIO_DIR, f"{file_uuid}.wav")
            shutil.copy(audio_path, dest_path)
            
            audio_files.append(dest_path)
            model_ids.append(model.id)
        
        # Create session
        session_id = str(uuid.uuid4())
        tts_sessions[session_id] = {
            "model_a": model_ids[0],
            "model_b": model_ids[1],
            "audio_a": audio_files[0],
            "audio_b": audio_files[1],
            "text": text,
            "created_at": datetime.utcnow(),
            "expires_at": datetime.utcnow() + timedelta(minutes=30),
            "voted": False
        }
        
        # Return audio file paths and session
        return jsonify({
            "session_id": session_id,
            "audio_a": f"/api/tts/audio/{session_id}/a",
            "audio_b": f"/api/tts/audio/{session_id}/b",
            "expires_in": 1800  # 30 minutes in seconds
        })
        
    except Exception as e:
        app.logger.error(f"TTS generation error: {str(e)}")
        return jsonify({"error": "Failed to generate TTS"}), 500


@app.route("/api/tts/audio/<session_id>/<model_key>")
def get_audio(session_id, model_key):
    if session_id not in tts_sessions:
        return jsonify({"error": "Invalid or expired session"}), 404
    
    session = tts_sessions[session_id]
    
    # Check if session expired
    if datetime.utcnow() > session["expires_at"]:
        cleanup_session(session_id)
        return jsonify({"error": "Session expired"}), 410
    
    if model_key == "a":
        audio_path = session["audio_a"]
    elif model_key == "b":
        audio_path = session["audio_b"]
    else:
        return jsonify({"error": "Invalid model key"}), 400
    
    # Check if file exists
    if not os.path.exists(audio_path):
        return jsonify({"error": "Audio file not found"}), 404
    
    return send_file(audio_path, mimetype="audio/wav")


@app.route("/api/tts/vote", methods=["POST"])
@limiter.limit("30 per minute")
def submit_vote():
    data = request.json
    session_id = data.get("session_id")
    chosen_model = data.get("chosen_model")  # "a" or "b"
    
    if not session_id or session_id not in tts_sessions:
        return jsonify({"error": "Invalid or expired session"}), 404
    
    if not chosen_model or chosen_model not in ["a", "b"]:
        return jsonify({"error": "Invalid chosen model"}), 400
    
    session = tts_sessions[session_id]
    
    # Check if session expired
    if datetime.utcnow() > session["expires_at"]:
        cleanup_session(session_id)
        return jsonify({"error": "Session expired"}), 410
    
    # Check if already voted
    if session["voted"]:
        return jsonify({"error": "Vote already submitted for this session"}), 400
    
    # Get model IDs
    chosen_id = session["model_a"] if chosen_model == "a" else session["model_b"]
    rejected_id = session["model_b"] if chosen_model == "a" else session["model_a"]
    
    # Record vote in database
    user_id = current_user.id if current_user.is_authenticated else None
    vote, error = record_vote(user_id, session["text"], chosen_id, rejected_id, ModelType.TTS)
    
    if error:
        return jsonify({"error": error}), 500
    
    # Mark session as voted
    session["voted"] = True
    
    # Return updated models
    return jsonify({
        "success": True,
        "chosen_model": {
            "id": chosen_id,
            "name": Model.query.get(chosen_id).name
        },
        "rejected_model": {
            "id": rejected_id,
            "name": Model.query.get(rejected_id).name
        }
    })


def cleanup_session(session_id):
    """Remove session and its audio files"""
    if session_id in tts_sessions:
        session = tts_sessions[session_id]
        
        # Remove audio files
        for audio_file in [session["audio_a"], session["audio_b"]]:
            if os.path.exists(audio_file):
                try:
                    os.remove(audio_file)
                except Exception as e:
                    app.logger.error(f"Error removing audio file: {str(e)}")
        
        # Remove session
        del tts_sessions[session_id]


# Schedule periodic cleanup
def setup_cleanup():
    def cleanup_expired_sessions():
        current_time = datetime.utcnow()
        expired_sessions = [
            sid for sid, session in tts_sessions.items()
            if current_time > session["expires_at"]
        ]
        for sid in expired_sessions:
            cleanup_session(sid)
    
    # Run cleanup every 15 minutes
    from apscheduler.schedulers.background import BackgroundScheduler
    scheduler = BackgroundScheduler()
    scheduler.add_job(cleanup_expired_sessions, 'interval', minutes=15)
    scheduler.start()


def setup_database_sync():
    """Setup database synchronization with HF dataset for Spaces"""
    if not IS_SPACES:
        return
    
    import os.path
    from huggingface_hub import HfApi, hf_hub_download
    from apscheduler.schedulers.background import BackgroundScheduler
    
    db_path = app.config["SQLALCHEMY_DATABASE_URI"].replace("sqlite:///", "instance/")
    
    def sync_database():
        try:
            # Upload the database to HF dataset
            api = HfApi(token=os.getenv("HF_TOKEN"))
            api.upload_file(
                path_or_fileobj=db_path,
                path_in_repo="tts_arena.db",
                repo_id="TTS-AGI/database-arena-v2",
                repo_type="dataset"
            )
            print(f"Database uploaded to HF dataset at {datetime.utcnow()}")
        except Exception as e:
            print(f"Error uploading database to HF dataset: {str(e)}")
    
    # Download database if it doesn't exist
    if not os.path.exists(db_path):
        try:
            print("Database not found, downloading from HF dataset...")
            hf_hub_download(
                repo_id="TTS-AGI/database-arena-v2",
                filename="tts_arena.db",
                repo_type="dataset",
                local_dir=os.path.dirname(db_path),
                token=os.getenv("HF_TOKEN")
            )
            print("Database downloaded successfully")
        except Exception as e:
            print(f"Error downloading database from HF dataset: {str(e)}")
    
    # Schedule periodic uploads
    scheduler = BackgroundScheduler()
    scheduler.add_job(sync_database, 'interval', minutes=5)
    scheduler.start()
    print("Database sync scheduler started")


@app.cli.command("init-db")
def init_db():
    """Initialize the database."""
    with app.app_context():
        db.create_all()
        print("Database initialized!")


if __name__ == "__main__":
    with app.app_context():
        db.create_all()  # Create tables if they don't exist
        insert_initial_models()
        # Call setup_cleanup to start the background scheduler
        setup_cleanup()
        # Setup database sync for HF Spaces
        setup_database_sync()
        
    # Configure Flask to recognize HTTPS when behind a reverse proxy
    from werkzeug.middleware.proxy_fix import ProxyFix
    
    # Apply ProxyFix middleware to handle reverse proxy headers
    # This ensures Flask generates correct URLs with https scheme
    # X-Forwarded-Proto header will be used to detect the original protocol
    app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)
    
    # Force Flask to prefer HTTPS for generated URLs
    app.config['PREFERRED_URL_SCHEME'] = 'https'
    from waitress import serve
    serve(app, host='0.0.0.0', port=int(os.environ.get("PORT", 7860)))