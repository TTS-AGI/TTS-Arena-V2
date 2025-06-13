"""
Security utilities for TTS Arena to prevent vote manipulation and botting.
"""

from datetime import datetime, timedelta
from models import db, Vote, User
from sqlalchemy import func, and_, or_
import logging

logger = logging.getLogger(__name__)


def detect_suspicious_voting_patterns(user_id, hours_back=24, max_votes_per_hour=30):
    """
    Detect if a user has suspicious voting patterns.
    Updated to allow rapid voting for reasonable periods (30 votes/hour = 1 vote every 2 minutes)
    Returns (is_suspicious, reason, vote_count)
    """
    if not user_id:
        return False, None, 0
    
    # Check voting frequency over 24 hours
    time_threshold = datetime.utcnow() - timedelta(hours=hours_back)
    recent_votes = Vote.query.filter(
        and_(
            Vote.user_id == user_id,
            Vote.vote_date >= time_threshold
        )
    ).count()
    
    # Allow up to 30 votes per hour (720 votes in 24 hours)
    # This allows rapid voting for several hours but catches extended botting
    max_votes_24h = max_votes_per_hour * hours_back
    
    if recent_votes > max_votes_24h:
        return True, f"Too many votes: {recent_votes} in {hours_back} hours (max: {max_votes_24h})", recent_votes
    
    # Additional check: if someone votes more than 100 times in 3 hours, that's suspicious
    # (100 votes in 3 hours = 1 vote every 1.8 minutes, which is very sustained)
    if hours_back >= 3:
        three_hour_threshold = datetime.utcnow() - timedelta(hours=3)
        votes_3h = Vote.query.filter(
            and_(
                Vote.user_id == user_id,
                Vote.vote_date >= three_hour_threshold
            )
        ).count()
        
        if votes_3h > 100:
            return True, f"Excessive voting in short period: {votes_3h} votes in 3 hours", recent_votes
    
    return False, None, recent_votes


def detect_model_bias(user_id, model_id, min_votes=5, bias_threshold=0.8):
    """
    Detect if a user consistently votes for a specific model.
    Returns (is_biased, bias_ratio, total_votes_for_model, total_votes)
    """
    if not user_id:
        return False, 0, 0, 0
    
    # Get all votes by this user
    total_votes = Vote.query.filter_by(user_id=user_id).count()
    
    if total_votes < min_votes:
        return False, 0, 0, total_votes
    
    # Get votes where this user chose the specific model
    votes_for_model = Vote.query.filter(
        and_(
            Vote.user_id == user_id,
            Vote.model_chosen == model_id
        )
    ).count()
    
    bias_ratio = votes_for_model / total_votes if total_votes > 0 else 0
    
    is_biased = bias_ratio >= bias_threshold and total_votes >= min_votes
    
    return is_biased, bias_ratio, votes_for_model, total_votes


def detect_coordinated_voting(model_id, hours_back=6, min_users=3, vote_threshold=10):
    """
    Detect coordinated voting campaigns for a specific model.
    Returns (is_coordinated, user_count, vote_count, suspicious_users)
    """
    time_threshold = datetime.utcnow() - timedelta(hours=hours_back)
    
    # Get recent votes for this model
    recent_votes = db.session.query(Vote.user_id).filter(
        and_(
            Vote.model_chosen == model_id,
            Vote.vote_date >= time_threshold
        )
    ).all()
    
    if len(recent_votes) < vote_threshold:
        return False, 0, len(recent_votes), []
    
    # Count unique users
    unique_users = set(vote.user_id for vote in recent_votes if vote.user_id)
    user_count = len(unique_users)
    
    # Check if multiple users are voting for the same model in a short time
    if user_count >= min_users and len(recent_votes) >= vote_threshold:
        # Get user details for suspicious users
        suspicious_users = []
        for user_id in unique_users:
            user_votes_for_model = Vote.query.filter(
                and_(
                    Vote.user_id == user_id,
                    Vote.model_chosen == model_id,
                    Vote.vote_date >= time_threshold
                )
            ).count()
            
            if user_votes_for_model > 1:  # Multiple votes for same model in short time
                user = User.query.get(user_id)
                if user:
                    suspicious_users.append({
                        'user_id': user_id,
                        'username': user.username,
                        'votes_for_model': user_votes_for_model,
                        'account_age_days': (datetime.utcnow() - user.join_date).days if user.join_date else None
                    })
        
        return True, user_count, len(recent_votes), suspicious_users
    
    return False, user_count, len(recent_votes), []


def detect_rapid_voting(user_id, min_interval_seconds=3):
    """
    Detect if a user is voting too rapidly (potential bot behavior).
    This allows rapid voting (3+ seconds) for reasonable periods, but flags
    extended periods of very rapid voting that indicate bot behavior.
    Returns (is_rapid, intervals, avg_interval)
    """
    if not user_id:
        return False, [], 0
    
    # Get more recent votes to better analyze patterns (last 50 instead of 10)
    recent_votes = Vote.query.filter_by(user_id=user_id).order_by(
        Vote.vote_date.desc()
    ).limit(50).all()
    
    if len(recent_votes) < 50:  # Need at least 50 votes to detect patterns
        return False, [], 0
    
    # Calculate intervals between votes
    intervals = []
    for i in range(len(recent_votes) - 1):
        interval = (recent_votes[i].vote_date - recent_votes[i + 1].vote_date).total_seconds()
        intervals.append(interval)
    
    avg_interval = sum(intervals) / len(intervals) if intervals else 0
    
    # More sophisticated bot detection:
    # 1. Count votes with intervals < 3 seconds (very rapid)
    very_rapid_votes = sum(1 for interval in intervals if interval < 3)
    
    # 2. Count votes with intervals < 1 second (extremely rapid - likely bot)
    extremely_rapid_votes = sum(1 for interval in intervals if interval < 1)
    
    # 3. Check for sustained rapid voting patterns
    # Look for sequences of 10+ votes all under 5 seconds
    sustained_rapid_sequences = 0
    current_sequence = 0
    for interval in intervals:
        if interval < 5:
            current_sequence += 1
        else:
            if current_sequence >= 10:  # 10+ votes in a row under 5 seconds
                sustained_rapid_sequences += 1
            current_sequence = 0
    
    # Final check for remaining sequence
    if current_sequence >= 10:
        sustained_rapid_sequences += 1
    
    # Flag as rapid/bot if:
    # - More than 20% of votes are extremely rapid (< 1 second) OR
    # - More than 60% of votes are very rapid (< 3 seconds) AND there are sustained sequences OR
    # - There are multiple sustained rapid sequences (10+ votes under 5 seconds each)
    total_intervals = len(intervals)
    extremely_rapid_ratio = extremely_rapid_votes / total_intervals if total_intervals > 0 else 0
    very_rapid_ratio = very_rapid_votes / total_intervals if total_intervals > 0 else 0
    
    is_rapid = (
        extremely_rapid_ratio > 0.2 or  # > 20% extremely rapid
        (very_rapid_ratio > 0.6 and sustained_rapid_sequences > 0) or  # > 60% very rapid + sustained
        sustained_rapid_sequences >= 2  # Multiple sustained rapid sequences
    )
    
    return is_rapid, intervals, avg_interval


def check_user_security_score(user_id):
    """
    Calculate a security score for a user based on various factors.
    Returns (score, factors) where score is 0-100 (higher = more trustworthy)
    """
    if not user_id:
        return 0, {"error": "No user ID provided"}
    
    user = User.query.get(user_id)
    if not user:
        return 0, {"error": "User not found"}
    
    factors = {}
    score = 100  # Start with perfect score and deduct points
    
    # Account age factor
    if user.join_date:
        account_age_days = (datetime.utcnow() - user.join_date).days
        factors['account_age_days'] = account_age_days
        if account_age_days < 45:
            score -= 30
        elif account_age_days < 90:
            score -= 15
        elif account_age_days < 180:
            score -= 5
    else:
        score -= 20
        factors['account_age_days'] = None
    
    # HF account age factor
    if user.hf_account_created:
        hf_age_days = (datetime.utcnow() - user.hf_account_created).days
        factors['hf_account_age_days'] = hf_age_days
        if hf_age_days < 30:
            score -= 25  # This should be caught by auth, but double-check
        elif hf_age_days < 90:
            score -= 10
    else:
        score -= 15
        factors['hf_account_age_days'] = None
    
    # Voting pattern analysis
    is_suspicious, reason, vote_count = detect_suspicious_voting_patterns(user_id)
    factors['suspicious_voting'] = is_suspicious
    factors['recent_vote_count'] = vote_count
    if is_suspicious:
        score -= 25
        factors['suspicious_reason'] = reason
    
    # Rapid voting check
    is_rapid, intervals, avg_interval = detect_rapid_voting(user_id)
    factors['rapid_voting'] = is_rapid
    factors['avg_vote_interval'] = avg_interval
    if is_rapid:
        score -= 20
    
    # Total vote count (very new users with many votes are suspicious)
    total_votes = Vote.query.filter_by(user_id=user_id).count()
    factors['total_votes'] = total_votes
    
    if account_age_days and account_age_days < 7 and total_votes > 20:
        score -= 15  # New account with many votes
    
    # Model bias detection - check for extreme bias toward any single model
    if total_votes >= 5:  # Only check if user has enough votes
        max_bias_ratio = 0
        most_biased_model = None
        
        # Get all models this user has voted for
        user_votes = Vote.query.filter_by(user_id=user_id).all()
        model_stats = {}
        
        for vote in user_votes:
            chosen_id = vote.model_chosen
            rejected_id = vote.model_rejected
            
            # Track appearances and choices
            if chosen_id not in model_stats:
                model_stats[chosen_id] = {'chosen': 0, 'appeared': 0}
            if rejected_id not in model_stats:
                model_stats[rejected_id] = {'chosen': 0, 'appeared': 0}
            
            model_stats[chosen_id]['chosen'] += 1
            model_stats[chosen_id]['appeared'] += 1
            model_stats[rejected_id]['appeared'] += 1
        
        # Find the highest bias ratio
        for model_id, stats in model_stats.items():
            if stats['appeared'] >= 5:  # Only consider models with enough appearances
                bias_ratio = stats['chosen'] / stats['appeared']
                if bias_ratio > max_bias_ratio:
                    max_bias_ratio = bias_ratio
                    most_biased_model = model_id
        
        factors['max_bias_ratio'] = max_bias_ratio
        factors['most_biased_model_id'] = most_biased_model
        
        # Deduct points based on bias level
        if max_bias_ratio >= 0.95:  # 95%+ bias
            score -= 30
            factors['bias_penalty'] = 'Extreme bias (95%+)'
        elif max_bias_ratio >= 0.9:  # 90%+ bias
            score -= 20
            factors['bias_penalty'] = 'Very high bias (90%+)'
        elif max_bias_ratio >= 0.8:  # 80%+ bias
            score -= 10
            factors['bias_penalty'] = 'High bias (80%+)'
        else:
            factors['bias_penalty'] = None
    else:
        factors['max_bias_ratio'] = 0
        factors['bias_penalty'] = None
    
    # Ensure score doesn't go below 0
    score = max(0, score)
    factors['final_score'] = score
    
    return score, factors


def is_vote_allowed(user_id, ip_address=None):
    """
    Check if a vote should be allowed based on security factors.
    Returns (allowed, reason, security_score)
    """
    if not user_id:
        return False, "User not authenticated", 0
    
    # Check security score
    score, factors = check_user_security_score(user_id)
    
    # Very low scores are blocked
    if score < 20:
        return False, f"Security score too low: {score}/100", score
    
    # Check for recent suspicious activity
    if factors.get('suspicious_voting'):
        return False, f"Suspicious voting pattern detected: {factors.get('suspicious_reason')}", score
    
    if factors.get('rapid_voting'):
        return False, f"Voting too rapidly (avg interval: {factors.get('avg_vote_interval', 0):.1f}s)", score
    
    # Additional IP-based checks could go here
    
    return True, "Vote allowed", score 