from flask import Blueprint, redirect, url_for, session, request, current_app, flash
from flask_login import login_user, logout_user, current_user, login_required
from authlib.integrations.flask_client import OAuth
import os
from models import db, User
import requests
from functools import wraps

auth = Blueprint("auth", __name__)
oauth = OAuth()


def init_oauth(app):
    oauth.init_app(app)
    oauth.register(
        name="huggingface",
        client_id=os.getenv("OAUTH_CLIENT_ID"),
        client_secret=os.getenv("OAUTH_CLIENT_SECRET"),
        access_token_url="https://huggingface.co/oauth/token",
        access_token_params=None,
        authorize_url="https://huggingface.co/oauth/authorize",
        authorize_params=None,
        api_base_url="https://huggingface.co/api/",
        client_kwargs={},
    )


def is_admin(user):
    """Check if a user is in the ADMIN_USERS environment variable"""
    if not user or not user.is_authenticated:
        return False
    
    admin_users = os.getenv("ADMIN_USERS", "").split(",")
    return user.username in [username.strip() for username in admin_users]


def admin_required(f):
    """Decorator to require admin access for a route"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated:
            flash("Please log in to access this page", "error")
            return redirect(url_for("auth.login", next=request.url))
        
        if not is_admin(current_user):
            flash("You do not have permission to access this page", "error")
            return redirect(url_for("arena"))
            
        return f(*args, **kwargs)
    return decorated_function


@auth.route("/login")
def login():
    # Store the next URL to redirect after login
    next_url = request.args.get("next") or url_for("arena")
    session["next_url"] = next_url

    redirect_uri = url_for("auth.authorize", _external=True, _scheme="https")
    return oauth.huggingface.authorize_redirect(redirect_uri)


@auth.route("/authorize")
def authorize():
    try:
        # Get token without OpenID verification
        token = oauth.huggingface.authorize_access_token()

        # Fetch user info manually from HF API
        headers = {"Authorization": f'Bearer {token["access_token"]}'}
        resp = requests.get("https://huggingface.co/api/whoami-v2", headers=headers)

        if not resp.ok:
            flash("Failed to fetch user information from Hugging Face", "error")
            return redirect(url_for("arena"))

        user_info = resp.json()

        # Check if user exists, otherwise create
        user = User.query.filter_by(hf_id=user_info["id"]).first()
        if not user:
            user = User(username=user_info["name"], hf_id=user_info["id"])
            db.session.add(user)
            db.session.commit()

        # Log in the user
        login_user(user, remember=True)

        # Redirect to the original page or default
        next_url = session.pop("next_url", url_for("arena"))
        return redirect(next_url)

    except Exception as e:
        current_app.logger.error(f"OAuth error: {str(e)}")
        flash(f"Authentication error: {str(e)}", "error")
        return redirect(url_for("arena"))


@auth.route("/logout")
@login_required
def logout():
    logout_user()
    flash("You have been logged out", "info")
    return redirect(url_for("arena"))
