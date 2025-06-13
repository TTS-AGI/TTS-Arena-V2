from flask import Blueprint, render_template, current_app, jsonify, request, redirect, url_for, flash
from models import db, User, Model, Vote, EloHistory, ModelType
from auth import admin_required
from security import check_user_security_score
from sqlalchemy import func, desc, extract, text
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
    
    # Calculate security scores for all users
    users_with_scores = []
    for user in users:
        score, factors = check_user_security_score(user.id)
        users_with_scores.append({
            'user': user,
            'security_score': score,
            'security_factors': factors
        })
    
    # Sort by security score (lowest first to highlight problematic users)
    users_with_scores.sort(key=lambda x: x['security_score'])
    
    return render_template("admin/users.html", users_with_scores=users_with_scores, admin_users=admin_users)

@admin.route("/user/<int:user_id>")
@admin_required
def user_detail(user_id):
    """View user details"""
    user = User.query.get_or_404(user_id)
    
    # Get security score and factors
    security_score, security_factors = check_user_security_score(user_id)
    
    # Get user votes
    recent_votes = Vote.query.filter_by(user_id=user_id).order_by(Vote.vote_date.desc()).limit(20).all()
    
    # Get vote statistics
    tts_votes = Vote.query.filter_by(user_id=user_id, model_type=ModelType.TTS).count()
    conversational_votes = Vote.query.filter_by(user_id=user_id, model_type=ModelType.CONVERSATIONAL).count()
    
    # Get comprehensive model bias analysis
    # This counts how often each model was chosen vs how often it appeared
    model_bias_analysis = []
    
    # Get all votes by this user
    user_votes = Vote.query.filter_by(user_id=user_id).all()
    
    if user_votes:
        model_stats = {}
        
        for vote in user_votes:
            # Track model_chosen
            chosen_id = vote.model_chosen
            rejected_id = vote.model_rejected
            
            # Initialize model stats if not exists
            if chosen_id not in model_stats:
                model_stats[chosen_id] = {'chosen': 0, 'appeared': 0, 'name': None}
            if rejected_id not in model_stats:
                model_stats[rejected_id] = {'chosen': 0, 'appeared': 0, 'name': None}
            
            # Count appearances and choices
            model_stats[chosen_id]['chosen'] += 1
            model_stats[chosen_id]['appeared'] += 1
            model_stats[rejected_id]['appeared'] += 1
        
        # Get model names and calculate bias ratios
        for model_id, stats in model_stats.items():
            model = Model.query.get(model_id)
            if model:
                stats['name'] = model.name
                stats['bias_ratio'] = stats['chosen'] / stats['appeared'] if stats['appeared'] > 0 else 0
                stats['model_id'] = model_id
        
        # Sort by bias ratio (highest bias first) and take top 5
        model_bias_analysis = sorted(
            [stats for stats in model_stats.values() if stats['name'] is not None],
            key=lambda x: x['bias_ratio'],
            reverse=True
        )[:5]
    
    return render_template(
        "admin/user_detail.html",
        user=user,
        security_score=security_score,
        security_factors=security_factors,
        recent_votes=recent_votes,
        tts_votes=tts_votes,
        conversational_votes=conversational_votes,
        model_bias_analysis=model_bias_analysis,
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
    current_time = datetime.utcnow()
    last_24h = current_time.replace(minute=0, second=0, microsecond=0) - timedelta(hours=24)
    
    # Use SQLite-compatible date formatting
    hourly_votes = db.session.query(
        func.strftime('%Y-%m-%d %H:00', Vote.vote_date).label('hour'),
        func.count().label('count')
    ).filter(
        Vote.vote_date >= last_24h
    ).group_by('hour').order_by('hour').all()
    
    # Generate all hours for the past 24 hours with correct hour formatting
    hour_list = []
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

@admin.route("/analytics")
@admin_required
def analytics():
    """View analytics data including session duration, IP addresses, etc."""
    
    # Get analytics statistics
    analytics_stats = {}
    
    try:
        # Session duration statistics
        duration_stats = db.session.execute(text("""
            SELECT 
                AVG(session_duration_seconds) as avg_duration,
                MIN(session_duration_seconds) as min_duration,
                MAX(session_duration_seconds) as max_duration,
                COUNT(session_duration_seconds) as total_with_duration
            FROM vote 
            WHERE session_duration_seconds IS NOT NULL
        """)).fetchone()
        
        analytics_stats['duration'] = {
            'avg': round(duration_stats.avg_duration, 2) if duration_stats.avg_duration else 0,
            'min': round(duration_stats.min_duration, 2) if duration_stats.min_duration else 0,
            'max': round(duration_stats.max_duration, 2) if duration_stats.max_duration else 0,
            'total': duration_stats.total_with_duration or 0
        }
        
        # Cache hit statistics
        cache_stats = db.session.execute(text("""
            SELECT 
                cache_hit,
                COUNT(*) as count
            FROM vote 
            WHERE cache_hit IS NOT NULL
            GROUP BY cache_hit
        """)).fetchall()
        
        analytics_stats['cache'] = {
            'hits': 0,
            'misses': 0,
            'total': 0
        }
        
        for stat in cache_stats:
            if stat.cache_hit:
                analytics_stats['cache']['hits'] = stat.count
            else:
                analytics_stats['cache']['misses'] = stat.count
            analytics_stats['cache']['total'] += stat.count
        
        # Top IP address regions (anonymized)
        ip_stats = db.session.execute(text("""
            SELECT 
                ip_address_partial,
                COUNT(*) as count
            FROM vote 
            WHERE ip_address_partial IS NOT NULL
            GROUP BY ip_address_partial
            ORDER BY count DESC
            LIMIT 10
        """)).fetchall()
        
        analytics_stats['top_ips'] = [
            {'ip': stat.ip_address_partial, 'count': stat.count}
            for stat in ip_stats
        ]
        
        # User agent statistics (top browsers/devices)
        ua_stats = db.session.execute(text("""
            SELECT 
                CASE 
                    WHEN user_agent LIKE '%Chrome%' THEN 'Chrome'
                    WHEN user_agent LIKE '%Firefox%' THEN 'Firefox'
                    WHEN user_agent LIKE '%Safari%' AND user_agent NOT LIKE '%Chrome%' THEN 'Safari'
                    WHEN user_agent LIKE '%Edge%' THEN 'Edge'
                    WHEN user_agent LIKE '%Mobile%' OR user_agent LIKE '%Android%' THEN 'Mobile'
                    ELSE 'Other'
                END as browser,
                COUNT(*) as count
            FROM vote 
            WHERE user_agent IS NOT NULL
            GROUP BY browser
            ORDER BY count DESC
        """)).fetchall()
        
        analytics_stats['browsers'] = [
            {'browser': stat.browser, 'count': stat.count}
            for stat in ua_stats
        ]
        
        # Recent votes with analytics data
        recent_analytics = db.session.execute(text("""
            SELECT 
                v.id,
                v.vote_date,
                v.session_duration_seconds,
                v.ip_address_partial,
                v.cache_hit,
                v.model_type,
                u.username,
                m1.name as chosen_model,
                m2.name as rejected_model
            FROM vote v
            LEFT JOIN user u ON v.user_id = u.id
            LEFT JOIN model m1 ON v.model_chosen = m1.id
            LEFT JOIN model m2 ON v.model_rejected = m2.id
            WHERE v.session_duration_seconds IS NOT NULL
            ORDER BY v.vote_date DESC
            LIMIT 20
        """)).fetchall()
        
        analytics_stats['recent_votes'] = [
            {
                'id': vote.id,
                'vote_date': vote.vote_date,
                'duration': round(vote.session_duration_seconds, 2) if vote.session_duration_seconds else None,
                'ip': vote.ip_address_partial,
                'cache_hit': vote.cache_hit,
                'model_type': vote.model_type,
                'username': vote.username,
                'chosen_model': vote.chosen_model,
                'rejected_model': vote.rejected_model
            }
            for vote in recent_analytics
        ]
        
    except Exception as e:
        flash(f"Error retrieving analytics data: {str(e)}", "error")
        analytics_stats = {}
    
    return render_template(
        "admin/analytics.html", 
        analytics_stats=analytics_stats
    )

@admin.route("/security")
@admin_required
def security():
    """View security monitoring data and suspicious activity."""
    try:
        from security import (
            detect_suspicious_voting_patterns, 
            detect_coordinated_voting, 
            check_user_security_score,
            detect_model_bias
        )
        
        # Get recent suspicious users
        recent_users = User.query.order_by(User.join_date.desc()).limit(50).all()
        suspicious_users = []
        
        for user in recent_users:
            score, factors = check_user_security_score(user.id)
            if score < 50:  # Flag users with low security scores
                suspicious_users.append({
                    'user': user,
                    'score': score,
                    'factors': factors
                })
        
        # Sort by lowest score first
        suspicious_users.sort(key=lambda x: x['score'])
        
        # Check for coordinated voting on top models
        top_models = Model.query.order_by(Model.current_elo.desc()).limit(10).all()
        coordinated_campaigns = []
        
        for model in top_models:
            is_coordinated, user_count, vote_count, suspicious_users_list = detect_coordinated_voting(model.id)
            if is_coordinated:
                coordinated_campaigns.append({
                    'model': model,
                    'user_count': user_count,
                    'vote_count': vote_count,
                    'suspicious_users': suspicious_users_list
                })
        
        # Get users with high model bias
        biased_users = []
        for model in top_models:
            # Check recent voters for this model
            recent_voters = db.session.query(Vote.user_id).filter(
                Vote.model_chosen == model.id
            ).distinct().limit(20).all()
            
            for voter in recent_voters:
                if voter.user_id:
                    is_biased, bias_ratio, votes_for_model, total_votes = detect_model_bias(
                        voter.user_id, model.id
                    )
                    if is_biased and total_votes >= 5:
                        user = User.query.get(voter.user_id)
                        if user:
                            biased_users.append({
                                'user': user,
                                'model': model,
                                'bias_ratio': bias_ratio,
                                'votes_for_model': votes_for_model,
                                'total_votes': total_votes
                            })
        
        # Remove duplicates and sort by bias ratio
        seen_users = set()
        unique_biased_users = []
        for item in biased_users:
            user_model_key = (item['user'].id, item['model'].id)
            if user_model_key not in seen_users:
                seen_users.add(user_model_key)
                unique_biased_users.append(item)
        
        unique_biased_users.sort(key=lambda x: x['bias_ratio'], reverse=True)
        
        # Get recent security blocks from logs (if available)
        security_blocks = []
        try:
            # This would require parsing application logs
            # For now, we'll show a placeholder
            pass
        except Exception:
            pass
        
        return render_template(
            "admin/security.html",
            suspicious_users=suspicious_users[:20],  # Limit to top 20
            coordinated_campaigns=coordinated_campaigns,
            biased_users=unique_biased_users[:20],  # Limit to top 20
            security_blocks=security_blocks
        )
        
    except ImportError:
        flash("Security module not available", "error")
        return redirect(url_for("admin.index"))
    except Exception as e:
        flash(f"Error loading security data: {str(e)}", "error")
        return redirect(url_for("admin.index")) 