from flask import Blueprint, render_template, current_app, jsonify, request, redirect, url_for, flash
from models import db, User, Model, Vote, EloHistory, ModelType
from auth import admin_required
from sqlalchemy import func, desc, extract
from datetime import datetime, timedelta
import json
import os

admin = Blueprint("admin", __name__, url_prefix="/admin")

@admin.route("/")
@admin_required
def index():
    """Admin dashboard homepage"""
    # Get count statistics
    stats = {
        "total_users": User.query.count(),
        "total_votes": Vote.query.count(),
        "tts_votes": Vote.query.filter_by(model_type=ModelType.TTS).count(),
        "conversational_votes": Vote.query.filter_by(model_type=ModelType.CONVERSATIONAL).count(),
        "tts_models": Model.query.filter_by(model_type=ModelType.TTS).count(),
        "conversational_models": Model.query.filter_by(model_type=ModelType.CONVERSATIONAL).count(),
    }
    
    # Get recent votes
    recent_votes = Vote.query.order_by(Vote.vote_date.desc()).limit(10).all()
    
    # Get recent users
    recent_users = User.query.order_by(User.join_date.desc()).limit(10).all()
    
    # Get daily votes for the past 30 days
    thirty_days_ago = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=30)
    
    daily_votes = db.session.query(
        func.date(Vote.vote_date).label('date'),
        func.count().label('count')
    ).filter(Vote.vote_date >= thirty_days_ago).group_by(
        func.date(Vote.vote_date)
    ).order_by(func.date(Vote.vote_date)).all()
    
    # Generate a complete list of dates for the past 30 days
    date_list = []
    current_date = datetime.utcnow()
    for i in range(30, -1, -1):
        date_list.append((current_date - timedelta(days=i)).date())
    
    # Create a dictionary with actual vote counts
    vote_counts = {day.date: day.count for day in daily_votes}
    
    # Build complete datasets including days with zero votes
    formatted_dates = [date.strftime("%Y-%m-%d") for date in date_list]
    vote_counts_list = [vote_counts.get(date, 0) for date in date_list]
    
    daily_votes_data = {
        "labels": formatted_dates,
        "counts": vote_counts_list
    }
    
    # Get top models
    top_tts_models = Model.query.filter_by(
        model_type=ModelType.TTS
    ).order_by(Model.current_elo.desc()).limit(5).all()
    
    top_conversational_models = Model.query.filter_by(
        model_type=ModelType.CONVERSATIONAL
    ).order_by(Model.current_elo.desc()).limit(5).all()
    
    return render_template(
        "admin/index.html",
        stats=stats,
        recent_votes=recent_votes,
        recent_users=recent_users,
        daily_votes_data=json.dumps(daily_votes_data),
        top_tts_models=top_tts_models,
        top_conversational_models=top_conversational_models
    )

@admin.route("/models")
@admin_required
def models():
    """Manage models"""
    tts_models = Model.query.filter_by(model_type=ModelType.TTS).order_by(Model.name).all()
    conversational_models = Model.query.filter_by(model_type=ModelType.CONVERSATIONAL).order_by(Model.name).all()
    
    return render_template(
        "admin/models.html",
        tts_models=tts_models,
        conversational_models=conversational_models
    )


@admin.route("/model/<model_id>", methods=["GET", "POST"])
@admin_required
def edit_model(model_id):
    """Edit a model"""
    model = Model.query.get_or_404(model_id)
    
    if request.method == "POST":
        model.name = request.form.get("name")
        model.is_active = "is_active" in request.form
        model.is_open = "is_open" in request.form
        model.model_url = request.form.get("model_url")
        
        db.session.commit()
        flash(f"Model '{model.name}' updated successfully", "success")
        return redirect(url_for("admin.models"))
    
    return render_template("admin/edit_model.html", model=model)

@admin.route("/users")
@admin_required
def users():
    """Manage users"""
    users = User.query.order_by(User.username).all()
    admin_users = os.getenv("ADMIN_USERS", "").split(",")
    admin_users = [username.strip() for username in admin_users]
    
    return render_template("admin/users.html", users=users, admin_users=admin_users)

@admin.route("/user/<int:user_id>")
@admin_required
def user_detail(user_id):
    """View user details"""
    user = User.query.get_or_404(user_id)
    
    # Get user votes
    recent_votes = Vote.query.filter_by(user_id=user_id).order_by(Vote.vote_date.desc()).limit(20).all()
    
    # Get vote statistics
    tts_votes = Vote.query.filter_by(user_id=user_id, model_type=ModelType.TTS).count()
    conversational_votes = Vote.query.filter_by(user_id=user_id, model_type=ModelType.CONVERSATIONAL).count()
    
    # Get favorite models (most chosen)
    favorite_models = db.session.query(
        Vote.model_chosen,
        Model.name,
        func.count().label('count')
    ).join(
        Model, Vote.model_chosen == Model.id
    ).filter(
        Vote.user_id == user_id
    ).group_by(
        Vote.model_chosen, Model.name
    ).order_by(
        desc('count')
    ).limit(5).all()
    
    return render_template(
        "admin/user_detail.html",
        user=user,
        recent_votes=recent_votes,
        tts_votes=tts_votes,
        conversational_votes=conversational_votes,
        favorite_models=favorite_models,
        total_votes=tts_votes + conversational_votes
    )

@admin.route("/votes")
@admin_required
def votes():
    """View recent votes"""
    page = request.args.get('page', 1, type=int)
    per_page = 50
    
    # Get votes with pagination
    votes_pagination = Vote.query.order_by(
        Vote.vote_date.desc()
    ).paginate(page=page, per_page=per_page)
    
    return render_template(
        "admin/votes.html",
        votes=votes_pagination.items,
        pagination=votes_pagination
    )

@admin.route("/statistics")
@admin_required
def statistics():
    """View detailed statistics"""
    # Get daily votes for the past 30 days by model type
    thirty_days_ago = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=30)
    
    tts_daily_votes = db.session.query(
        func.date(Vote.vote_date).label('date'),
        func.count().label('count')
    ).filter(
        Vote.vote_date >= thirty_days_ago,
        Vote.model_type == ModelType.TTS
    ).group_by(
        func.date(Vote.vote_date)
    ).order_by(func.date(Vote.vote_date)).all()
    
    conv_daily_votes = db.session.query(
        func.date(Vote.vote_date).label('date'),
        func.count().label('count')
    ).filter(
        Vote.vote_date >= thirty_days_ago,
        Vote.model_type == ModelType.CONVERSATIONAL
    ).group_by(
        func.date(Vote.vote_date)
    ).order_by(func.date(Vote.vote_date)).all()
    
    # Monthly new users
    monthly_users = db.session.query(
        extract('year', User.join_date).label('year'),
        extract('month', User.join_date).label('month'),
        func.count().label('count')
    ).group_by(
        'year', 'month'
    ).order_by('year', 'month').all()
    
    # Generate a complete list of dates for the past 30 days
    date_list = []
    current_date = datetime.utcnow()
    for i in range(30, -1, -1):
        date_list.append((current_date - timedelta(days=i)).date())
    
    # Create dictionaries with actual vote counts
    tts_vote_counts = {day.date: day.count for day in tts_daily_votes}
    conv_vote_counts = {day.date: day.count for day in conv_daily_votes}
    
    # Format dates consistently for charts
    formatted_dates = [date.strftime("%Y-%m-%d") for date in date_list]
    
    # Build complete datasets including days with zero votes
    tts_counts = [tts_vote_counts.get(date, 0) for date in date_list]
    conv_counts = [conv_vote_counts.get(date, 0) for date in date_list]
    
    # Generate all month/year combinations for the past 12 months
    current_date = datetime.utcnow()
    month_list = []
    for i in range(11, -1, -1):
        past_date = current_date - timedelta(days=i*30)  # Approximate
        month_list.append((past_date.year, past_date.month))
    
    # Create a dictionary with actual user counts
    user_counts = {(record.year, record.month): record.count for record in monthly_users}
    
    # Build complete monthly datasets including months with zero new users
    monthly_labels = [f"{month}/{year}" for year, month in month_list]
    monthly_counts = [user_counts.get((year, month), 0) for year, month in month_list]
    
    # Model performance over time
    top_models = Model.query.order_by(Model.match_count.desc()).limit(5).all()
    
    # Get first and last timestamp to create a consistent timeline
    earliest = datetime.utcnow() - timedelta(days=30)  # Default to 30 days ago
    latest = datetime.utcnow()  # Default to now
    
    # Find actual earliest and latest timestamps across all models
    has_elo_history = False
    for model in top_models:
        first = EloHistory.query.filter_by(model_id=model.id).order_by(EloHistory.timestamp).first()
        last = EloHistory.query.filter_by(model_id=model.id).order_by(EloHistory.timestamp.desc()).first()
        
        if first and last:
            has_elo_history = True
            if first.timestamp < earliest:
                earliest = first.timestamp
            if last.timestamp > latest:
                latest = last.timestamp
    
    # If no history was found, use a default range of the last 30 days
    if not has_elo_history:
        earliest = datetime.utcnow() - timedelta(days=30)
        latest = datetime.utcnow()
    
    # Make sure the date range is valid (earliest before latest)
    if earliest > latest:
        earliest = latest - timedelta(days=30)
    
    # Generate a list of dates for the ELO history timeline
    # Using 1-day intervals for a smoother chart
    elo_dates = []
    current = earliest
    while current <= latest:
        elo_dates.append(current.date())
        current += timedelta(days=1)
    
    # Format dates consistently
    formatted_elo_dates = [date.strftime("%Y-%m-%d") for date in elo_dates]
    
    model_history = {}
    
    # Initialize empty data for all top models
    for model in top_models:
        model_history[model.name] = {
            "timestamps": formatted_elo_dates,
            "scores": [None] * len(formatted_elo_dates)  # Initialize with None values
        }
        
        history = EloHistory.query.filter_by(
            model_id=model.id
        ).order_by(EloHistory.timestamp).all()
        
        if history:
            # Create a dictionary mapping dates to scores
            history_dict = {}
            for h in history:
                date_key = h.timestamp.date().strftime("%Y-%m-%d")
                history_dict[date_key] = h.elo_score
            
            # Fill in missing dates with the previous score
            last_score = model.current_elo  # Default to current ELO if no history
            scores = []
            
            for date in formatted_elo_dates:
                if date in history_dict:
                    last_score = history_dict[date]
                scores.append(last_score)
            
            model_history[model.name]["scores"] = scores
        else:
            # If no history, use the current Elo for all dates
            model_history[model.name]["scores"] = [model.current_elo] * len(formatted_elo_dates)
    
    chart_data = {
        "dailyVotes": {
            "labels": formatted_dates,
            "ttsCounts": tts_counts,
            "convCounts": conv_counts
        },
        "monthlyUsers": {
            "labels": monthly_labels,
            "counts": monthly_counts
        },
        "modelHistory": model_history
    }
    
    return render_template(
        "admin/statistics.html",
        chart_data=json.dumps(chart_data)
    )

@admin.route("/activity")
@admin_required
def activity():
    """View recent text generations"""
    # Check if we have any active sessions from app.py
    tts_session_count = 0
    conversational_session_count = 0
    
    # Access global variables from app.py through current_app
    if hasattr(current_app, 'tts_sessions'):
        tts_session_count = len(current_app.tts_sessions)
    else:  # Try to access through app module
        from app import tts_sessions
        tts_session_count = len(tts_sessions)
    
    if hasattr(current_app, 'conversational_sessions'):
        conversational_session_count = len(current_app.conversational_sessions)
    else:  # Try to access through app module
        from app import conversational_sessions
        conversational_session_count = len(conversational_sessions)
    
    # Get recent votes which represent completed generations
    recent_tts_votes = Vote.query.filter_by(
        model_type=ModelType.TTS
    ).order_by(Vote.vote_date.desc()).limit(20).all()
    
    recent_conv_votes = Vote.query.filter_by(
        model_type=ModelType.CONVERSATIONAL
    ).order_by(Vote.vote_date.desc()).limit(20).all()
    
    # Get votes per hour for the last 24 hours
    last_24h = datetime.utcnow() - timedelta(hours=24)
    
    # Use SQLite-compatible date formatting
    hourly_votes = db.session.query(
        func.strftime('%Y-%m-%d %H:00', Vote.vote_date).label('hour'),
        func.count().label('count')
    ).filter(
        Vote.vote_date >= last_24h
    ).group_by('hour').order_by('hour').all()
    
    # Generate all hours for the past 24 hours with correct hour formatting
    hour_list = []
    current_time = datetime.utcnow()
    
    for i in range(24, -1, -1):
        # Calculate the hour time and truncate to hour
        hour_time = current_time - timedelta(hours=i)
        hour_time = hour_time.replace(minute=0, second=0, microsecond=0)
        hour_list.append(hour_time.strftime('%Y-%m-%d %H:00'))
    
    # Create a dictionary with actual vote counts
    vote_counts = {hour.hour: hour.count for hour in hourly_votes}
    
    # Build complete hourly datasets including hours with zero votes
    hourly_data = {
        "labels": hour_list,
        "counts": [vote_counts.get(hour, 0) for hour in hour_list]
    }
    
    return render_template(
        "admin/activity.html",
        tts_session_count=tts_session_count,
        conversational_session_count=conversational_session_count,
        recent_tts_votes=recent_tts_votes,
        recent_conv_votes=recent_conv_votes,
        hourly_data=json.dumps(hourly_data)
    ) 