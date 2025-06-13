from flask import Blueprint, redirect, url_for, session, request, current_app, flash
from flask_login import login_user, logout_user, current_user, login_required
from authlib.integrations.flask_client import OAuth
import os
from models import db, User
import requests
from functools import wraps
from datetime import datetime, timedelta

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


def check_account_age(username, min_days=30):
    """
    Check if a Hugging Face account is at least min_days old.
    Returns (is_old_enough, created_date, error_message)
    """
    try:
        # Fetch user overview from HF API
        resp = requests.get(f"https://huggingface.co/api/users/{username}/overview", timeout=10)
        
        if not resp.ok:
            return False, None, f"Failed to fetch account information (HTTP {resp.status_code})"
        
        user_data = resp.json()
        
        if "createdAt" not in user_data:
            return False, None, "Account creation date not available"
        
        # Parse the creation date
        created_at_str = user_data["createdAt"]
        # Handle both formats: with and without milliseconds
        try:
            created_at = datetime.fromisoformat(created_at_str.replace('Z', '+00:00'))
        except ValueError:
            # Try without milliseconds
            created_at = datetime.strptime(created_at_str, "%Y-%m-%dT%H:%M:%S.%fZ")
        
        # Calculate account age
        account_age = datetime.utcnow() - created_at.replace(tzinfo=None)
        required_age = timedelta(days=min_days)
        
        is_old_enough = account_age >= required_age
        
        return is_old_enough, created_at, None
        
    except requests.RequestException as e:
        return False, None, f"Network error checking account age: {str(e)}"
    except Exception as e:
        return False, None, f"Error parsing account data: {str(e)}"


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
        username = user_info["name"]

        # Check account age requirement (30 days minimum)
        is_old_enough, created_date, error_msg = check_account_age(username, min_days=30)
        
        if error_msg:
            current_app.logger.warning(f"Account age check failed for {username}: {error_msg}")
            flash("Unable to verify account age. Please try again later.", "error")
            return redirect(url_for("arena"))
        
        if not is_old_enough:
            if created_date:
                account_age_days = (datetime.utcnow() - created_date.replace(tzinfo=None)).days
                flash(f"Your Hugging Face account must be at least 30 days old to use TTS Arena. Your account is {account_age_days} days old. Please try again later.", "error")
            else:
                flash("Your Hugging Face account must be at least 30 days old to use TTS Arena.", "error")
            return redirect(url_for("arena"))

        # Check if user exists, otherwise create
        user = User.query.filter_by(hf_id=user_info["id"]).first()
        if not user:
            user = User(
                username=username, 
                hf_id=user_info["id"],
                hf_account_created=created_date.replace(tzinfo=None) if created_date else None
            )
            db.session.add(user)
            db.session.commit()
            current_app.logger.info(f"Created new user account: {username} (HF account created: {created_date})")
        elif not user.hf_account_created and created_date:
            # Update existing users with missing creation date
            user.hf_account_created = created_date.replace(tzinfo=None)
            db.session.commit()
            current_app.logger.info(f"Updated HF account creation date for {username}: {created_date}")

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
