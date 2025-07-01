from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from datetime import datetime, timedelta
import math
from sqlalchemy import func, text
import logging
import hashlib

db = SQLAlchemy()


class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(100), unique=True, nullable=False)
    hf_id = db.Column(db.String(100), unique=True, nullable=False)
    join_date = db.Column(db.DateTime, default=datetime.utcnow)
    hf_account_created = db.Column(db.DateTime, nullable=True)  # HF account creation date
    votes = db.relationship("Vote", backref="user", lazy=True)
    show_in_leaderboard = db.Column(db.Boolean, default=True)

    def __repr__(self):
        return f"<User {self.username}>"


class ModelType:
    TTS = "tts"
    CONVERSATIONAL = "conversational"


class Model(db.Model):
    id = db.Column(db.String(100), primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    model_type = db.Column(db.String(20), nullable=False)  # 'tts' or 'conversational'
    # Fix ambiguous foreign keys by specifying which foreign key to use
    votes = db.relationship(
        "Vote",
        primaryjoin="or_(Model.id==Vote.model_chosen, Model.id==Vote.model_rejected)",
        viewonly=True,
    )
    current_elo = db.Column(db.Float, default=1500.0)
    win_count = db.Column(db.Integer, default=0)
    match_count = db.Column(db.Integer, default=0)
    is_open = db.Column(db.Boolean, default=False)
    is_active = db.Column(
        db.Boolean, default=True
    )  # Whether the model is active and can be voted on
    model_url = db.Column(db.String(255), nullable=True)

    @property
    def win_rate(self):
        if self.match_count == 0:
            return 0
        return (self.win_count / self.match_count) * 100

    def __repr__(self):
        return f"<Model {self.name} ({self.model_type})>"


class Vote(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    text = db.Column(db.String(1000), nullable=False)
    vote_date = db.Column(db.DateTime, default=datetime.utcnow)
    model_chosen = db.Column(db.String(100), db.ForeignKey("model.id"), nullable=False)
    model_rejected = db.Column(
        db.String(100), db.ForeignKey("model.id"), nullable=False
    )
    model_type = db.Column(db.String(20), nullable=False)  # 'tts' or 'conversational'
    
    # New analytics columns - added with temporary checks for migration
    session_duration_seconds = db.Column(db.Float, nullable=True)  # Time from generation to vote
    ip_address_partial = db.Column(db.String(20), nullable=True)  # IP with last digits removed
    user_agent = db.Column(db.String(500), nullable=True)  # Browser/device info
    generation_date = db.Column(db.DateTime, nullable=True)  # When audio was generated
    cache_hit = db.Column(db.Boolean, nullable=True)  # Whether generation was from cache
    
    # Sentence origin tracking
    sentence_hash = db.Column(db.String(64), nullable=True, index=True)  # SHA-256 hash of the sentence
    sentence_origin = db.Column(db.String(20), nullable=True)  # 'dataset', 'custom', 'unknown'
    counts_for_public_leaderboard = db.Column(db.Boolean, default=True)  # Whether this vote counts for public leaderboard

    chosen = db.relationship(
        "Model",
        foreign_keys=[model_chosen],
        backref=db.backref("chosen_votes", lazy=True),
    )
    rejected = db.relationship(
        "Model",
        foreign_keys=[model_rejected],
        backref=db.backref("rejected_votes", lazy=True),
    )

    def __repr__(self):
        return f"<Vote {self.id}: {self.model_chosen} over {self.model_rejected} ({self.model_type})>"


class EloHistory(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    model_id = db.Column(db.String(100), db.ForeignKey("model.id"), nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    elo_score = db.Column(db.Float, nullable=False)
    vote_id = db.Column(db.Integer, db.ForeignKey("vote.id"), nullable=True)
    model_type = db.Column(db.String(20), nullable=False)  # 'tts' or 'conversational'

    model = db.relationship("Model", backref=db.backref("elo_history", lazy=True))
    vote = db.relationship("Vote", backref=db.backref("elo_changes", lazy=True))

    def __repr__(self):
        return f"<EloHistory {self.model_id}: {self.elo_score} at {self.timestamp} ({self.model_type})>"


class CoordinatedVotingCampaign(db.Model):
    """Log detected coordinated voting campaigns"""
    id = db.Column(db.Integer, primary_key=True)
    model_id = db.Column(db.String(100), db.ForeignKey("model.id"), nullable=False)
    model_type = db.Column(db.String(20), nullable=False)
    detected_at = db.Column(db.DateTime, default=datetime.utcnow)
    time_window_hours = db.Column(db.Integer, nullable=False)  # Detection window (e.g., 6 hours)
    vote_count = db.Column(db.Integer, nullable=False)  # Total votes in the campaign
    user_count = db.Column(db.Integer, nullable=False)  # Number of users involved
    confidence_score = db.Column(db.Float, nullable=False)  # 0-1 confidence level
    status = db.Column(db.String(20), default='active')  # active, resolved, false_positive
    admin_notes = db.Column(db.Text, nullable=True)
    resolved_by = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    resolved_at = db.Column(db.DateTime, nullable=True)
    
    model = db.relationship("Model", backref=db.backref("coordinated_campaigns", lazy=True))
    resolver = db.relationship("User", backref=db.backref("resolved_campaigns", lazy=True))
    
    def __repr__(self):
        return f"<CoordinatedVotingCampaign {self.id}: {self.model_id} ({self.vote_count} votes, {self.user_count} users)>"


class CampaignParticipant(db.Model):
    """Track users involved in coordinated voting campaigns"""
    id = db.Column(db.Integer, primary_key=True)
    campaign_id = db.Column(db.Integer, db.ForeignKey("coordinated_voting_campaign.id"), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    votes_in_campaign = db.Column(db.Integer, nullable=False)
    first_vote_at = db.Column(db.DateTime, nullable=False)
    last_vote_at = db.Column(db.DateTime, nullable=False)
    suspicion_level = db.Column(db.String(20), nullable=False)  # low, medium, high
    
    campaign = db.relationship("CoordinatedVotingCampaign", backref=db.backref("participants", lazy=True))
    user = db.relationship("User", backref=db.backref("campaign_participations", lazy=True))
    
    def __repr__(self):
        return f"<CampaignParticipant {self.user_id} in campaign {self.campaign_id} ({self.votes_in_campaign} votes)>"


class UserTimeout(db.Model):
    """Track user timeouts/bans for suspicious activity"""
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    reason = db.Column(db.String(500), nullable=False)  # Reason for timeout
    timeout_type = db.Column(db.String(50), nullable=False)  # coordinated_voting, rapid_voting, manual, etc.
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    expires_at = db.Column(db.DateTime, nullable=False)
    created_by = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)  # Admin who created timeout
    is_active = db.Column(db.Boolean, default=True)
    cancelled_at = db.Column(db.DateTime, nullable=True)
    cancelled_by = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    cancel_reason = db.Column(db.String(500), nullable=True)
    
    # Related campaign if timeout was due to coordinated voting
    related_campaign_id = db.Column(db.Integer, db.ForeignKey("coordinated_voting_campaign.id"), nullable=True)
    
    user = db.relationship("User", foreign_keys=[user_id], backref=db.backref("timeouts", lazy=True))
    creator = db.relationship("User", foreign_keys=[created_by], backref=db.backref("created_timeouts", lazy=True))
    canceller = db.relationship("User", foreign_keys=[cancelled_by], backref=db.backref("cancelled_timeouts", lazy=True))
    related_campaign = db.relationship("CoordinatedVotingCampaign", backref=db.backref("resulting_timeouts", lazy=True))
    
    def is_currently_active(self):
        """Check if timeout is currently active"""
        if not self.is_active:
            return False
        return datetime.utcnow() < self.expires_at
    
    def __repr__(self):
        return f"<UserTimeout {self.user_id}: {self.timeout_type} until {self.expires_at}>"


class ConsumedSentence(db.Model):
    """Track sentences that have been used to ensure each sentence is only used once"""
    id = db.Column(db.Integer, primary_key=True)
    sentence_hash = db.Column(db.String(64), unique=True, nullable=False, index=True)  # SHA-256 hash
    sentence_text = db.Column(db.Text, nullable=False)  # Store original text for debugging/admin purposes
    consumed_at = db.Column(db.DateTime, default=datetime.utcnow)
    session_id = db.Column(db.String(100), nullable=True)  # Track which session consumed it
    usage_type = db.Column(db.String(20), nullable=False)  # 'cache', 'direct', 'random'
    
    def __repr__(self):
        return f"<ConsumedSentence {self.sentence_hash[:8]}...({self.usage_type})>"


def calculate_elo_change(winner_elo, loser_elo, k_factor=32):
    """Calculate Elo rating changes for a match."""
    expected_winner = 1 / (1 + math.pow(10, (loser_elo - winner_elo) / 400))
    expected_loser = 1 / (1 + math.pow(10, (winner_elo - loser_elo) / 400))

    winner_new_elo = winner_elo + k_factor * (1 - expected_winner)
    loser_new_elo = loser_elo + k_factor * (0 - expected_loser)

    return winner_new_elo, loser_new_elo


def anonymize_ip_address(ip_address):
    """
    Remove the last 1-2 octets from an IP address for privacy compliance.
    Examples:
    - 192.168.1.100 -> 192.168.0.0
    - 2001:db8::1 -> 2001:db8::
    """
    if not ip_address:
        return None
    
    try:
        if ':' in ip_address:  # IPv6
            # Keep first 4 groups, zero out the rest
            parts = ip_address.split(':')
            if len(parts) >= 4:
                return ':'.join(parts[:4]) + '::'
            return ip_address
        else:  # IPv4
            # Keep first 2 octets, zero out last 2
            parts = ip_address.split('.')
            if len(parts) == 4:
                return f"{parts[0]}.{parts[1]}.0.0"
            return ip_address
    except Exception:
        return None


def record_vote(user_id, text, chosen_model_id, rejected_model_id, model_type, 
                session_duration=None, ip_address=None, user_agent=None, 
                generation_date=None, cache_hit=None, all_dataset_sentences=None):
    """Record a vote and update Elo ratings."""
    
    # Determine sentence origin and whether it should count for public leaderboard
    sentence_hash = hash_sentence(text)
    sentence_origin = 'unknown'
    counts_for_public = True
    
    if all_dataset_sentences and text in all_dataset_sentences:
        sentence_origin = 'dataset'
        # Only count for public leaderboard if sentence was unconsumed when used
        # Check if it was consumed BEFORE this vote (don't consume yet)
        counts_for_public = not is_sentence_consumed(text)
    else:
        sentence_origin = 'custom'
        counts_for_public = False  # Custom sentences never count for public leaderboard
    
    # Create the vote
    vote = Vote(
        user_id=user_id,  # Required - user must be logged in to vote
        text=text,
        model_chosen=chosen_model_id,
        model_rejected=rejected_model_id,
        model_type=model_type,
        session_duration_seconds=session_duration,
        ip_address_partial=anonymize_ip_address(ip_address),
        user_agent=user_agent[:500] if user_agent else None,  # Truncate if too long
        generation_date=generation_date,
        cache_hit=cache_hit,
        sentence_hash=sentence_hash,
        sentence_origin=sentence_origin,
        counts_for_public_leaderboard=counts_for_public,
    )
    db.session.add(vote)
    db.session.flush()  # Get the vote ID without committing

    # Get the models
    chosen_model = Model.query.filter_by(
        id=chosen_model_id, model_type=model_type
    ).first()
    rejected_model = Model.query.filter_by(
        id=rejected_model_id, model_type=model_type
    ).first()

    if not chosen_model or not rejected_model:
        db.session.rollback()
        return None, "One or both models not found for the specified model type"

    # Only update Elo ratings and public stats if this vote counts for public leaderboard
    if counts_for_public:
        # Calculate new Elo ratings
        new_chosen_elo, new_rejected_elo = calculate_elo_change(
            chosen_model.current_elo, rejected_model.current_elo
        )

        # Update model stats
        chosen_model.current_elo = new_chosen_elo
        chosen_model.win_count += 1
        chosen_model.match_count += 1

        rejected_model.current_elo = new_rejected_elo
        rejected_model.match_count += 1
    else:
        # For votes that don't count for public leaderboard, keep current Elo
        new_chosen_elo = chosen_model.current_elo
        new_rejected_elo = rejected_model.current_elo

    # Record Elo history
    chosen_history = EloHistory(
        model_id=chosen_model_id,
        elo_score=new_chosen_elo,
        vote_id=vote.id,
        model_type=model_type,
    )

    rejected_history = EloHistory(
        model_id=rejected_model_id,
        elo_score=new_rejected_elo,
        vote_id=vote.id,
        model_type=model_type,
    )

    db.session.add_all([chosen_history, rejected_history])
    db.session.commit()

    return vote, None


def get_leaderboard_data(model_type):
    """
    Get leaderboard data for the specified model type.
    Only includes votes that count for the public leaderboard.

    Args:
        model_type (str): The model type ('tts' or 'conversational')

    Returns:
        list: List of dictionaries containing model data for the leaderboard
    """
    query = Model.query.filter_by(model_type=model_type)

    # Get models with >1k votes ordered by ELO score
    # Note: Model.match_count now only includes votes that count for public leaderboard
    models = query.filter(Model.match_count > 1000).order_by(Model.current_elo.desc()).all()

    result = []
    for rank, model in enumerate(models, 1):
        # Determine tier based on rank
        if rank <= 2:
            tier = "tier-s"
        elif rank <= 4:
            tier = "tier-a"
        elif rank <= 7:
            tier = "tier-b"
        else:
            tier = ""

        result.append(
            {
                "rank": rank,
                "id": model.id,
                "name": model.name,
                "model_url": model.model_url,
                "win_rate": f"{model.win_rate:.0f}%",
                "total_votes": model.match_count,
                "elo": int(model.current_elo),
                "tier": tier,
                "is_open": model.is_open,
            }
        )

    return result


def get_user_leaderboard(user_id, model_type):
    """
    Get personalized leaderboard data for a specific user.
    Includes ALL votes (both dataset and custom sentences).

    Args:
        user_id (int): The user ID
        model_type (str): The model type ('tts' or 'conversational')

    Returns:
        list: List of dictionaries containing model data for the user's personal leaderboard
    """
    # Get all models of the specified type
    models = Model.query.filter_by(model_type=model_type).all()

    # Get user's votes (includes both public and custom sentence votes)
    user_votes = Vote.query.filter_by(user_id=user_id, model_type=model_type).all()

    # Calculate win counts and match counts for each model based on user's votes
    model_stats = {model.id: {"wins": 0, "matches": 0} for model in models}

    for vote in user_votes:
        model_stats[vote.model_chosen]["wins"] += 1
        model_stats[vote.model_chosen]["matches"] += 1
        model_stats[vote.model_rejected]["matches"] += 1

    # Calculate win rates and prepare result
    result = []
    for model in models:
        stats = model_stats[model.id]
        win_rate = (
            (stats["wins"] / stats["matches"] * 100) if stats["matches"] > 0 else 0
        )

        # Only include models the user has voted on
        if stats["matches"] > 0:
            result.append(
                {
                    "id": model.id,
                    "name": model.name,
                    "model_url": model.model_url,
                    "win_rate": f"{win_rate:.0f}%",
                    "total_votes": stats["matches"],
                    "wins": stats["wins"],
                    "is_open": model.is_open,
                }
            )

    # Sort by win rate descending
    result.sort(key=lambda x: float(x["win_rate"].rstrip("%")), reverse=True)

    # Add rank
    for i, item in enumerate(result, 1):
        item["rank"] = i

    return result


def get_historical_leaderboard_data(model_type, target_date=None):
    """
    Get leaderboard data at a specific date in history.

    Args:
        model_type (str): The model type ('tts' or 'conversational')
        target_date (datetime): The target date for historical data, defaults to current time

    Returns:
        list: List of dictionaries containing model data for the historical leaderboard
    """
    if not target_date:
        target_date = datetime.utcnow()

    # Get all models of the specified type
    models = Model.query.filter_by(model_type=model_type).all()

    # Create a result list for the models
    result = []

    for model in models:
        # Get the most recent EloHistory entry for each model before the target date
        elo_entry = (
            EloHistory.query.filter(
                EloHistory.model_id == model.id,
                EloHistory.model_type == model_type,
                EloHistory.timestamp <= target_date,
            )
            .order_by(EloHistory.timestamp.desc())
            .first()
        )

        # Skip models that have no history before the target date
        if not elo_entry:
            continue

        # Count wins and matches up to the target date (only public leaderboard votes)
        match_count = Vote.query.filter(
            db.or_(Vote.model_chosen == model.id, Vote.model_rejected == model.id),
            Vote.model_type == model_type,
            Vote.vote_date <= target_date,
            Vote.counts_for_public_leaderboard == True,
        ).count()

        win_count = Vote.query.filter(
            Vote.model_chosen == model.id,
            Vote.model_type == model_type,
            Vote.vote_date <= target_date,
            Vote.counts_for_public_leaderboard == True,
        ).count()

        # Calculate win rate
        win_rate = (win_count / match_count * 100) if match_count > 0 else 0

        # Add to result
        result.append(
            {
                "id": model.id,
                "name": model.name,
                "model_url": model.model_url,
                "win_rate": f"{win_rate:.0f}%",
                "total_votes": match_count,
                "elo": int(elo_entry.elo_score),
                "is_open": model.is_open,
            }
        )

    # Sort by ELO score descending
    result.sort(key=lambda x: x["elo"], reverse=True)

    # Add rank and tier
    for i, item in enumerate(result, 1):
        item["rank"] = i
        # Determine tier based on rank
        if i <= 2:
            item["tier"] = "tier-s"
        elif i <= 4:
            item["tier"] = "tier-a"
        elif i <= 7:
            item["tier"] = "tier-b"
        else:
            item["tier"] = ""

    return result


def get_key_historical_dates(model_type):
    """
    Get a list of key dates in the leaderboard history.

    Args:
        model_type (str): The model type ('tts' or 'conversational')

    Returns:
        list: List of datetime objects representing key dates
    """
    # Get first and most recent vote dates
    first_vote = (
        Vote.query.filter_by(model_type=model_type)
        .order_by(Vote.vote_date.asc())
        .first()
    )
    last_vote = (
        Vote.query.filter_by(model_type=model_type)
        .order_by(Vote.vote_date.desc())
        .first()
    )

    if not first_vote or not last_vote:
        return []

    # Generate a list of key dates - first day of each month between the first and last vote
    dates = []
    current_date = first_vote.vote_date.replace(day=1)
    end_date = last_vote.vote_date

    while current_date <= end_date:
        dates.append(current_date)
        # Move to next month
        if current_date.month == 12:
            current_date = current_date.replace(year=current_date.year + 1, month=1)
        else:
            current_date = current_date.replace(month=current_date.month + 1)

    # Add latest date
    if dates and dates[-1].month != end_date.month or dates[-1].year != end_date.year:
        dates.append(end_date)

    return dates


def insert_initial_models():
    """Insert initial models into the database."""
    tts_models = [
        Model(
            id="eleven-multilingual-v2",
            name="Eleven Multilingual v2",
            model_type=ModelType.TTS,
            is_open=False,
            model_url="https://elevenlabs.io/",
        ),
        Model(
            id="eleven-turbo-v2.5",
            name="Eleven Turbo v2.5",
            model_type=ModelType.TTS,
            is_open=False,
            model_url="https://elevenlabs.io/",
        ),
        Model(
            id="eleven-flash-v2.5",
            name="Eleven Flash v2.5",
            model_type=ModelType.TTS,
            is_open=False,
            model_url="https://elevenlabs.io/",
        ),
        Model(
            id="cartesia-sonic-2",
            name="Cartesia Sonic 2",
            model_type=ModelType.TTS,
            is_open=False,
            is_active=False, # ran out of credits
            model_url="https://cartesia.ai/",
        ),
        Model(
            id="spark-tts",
            name="Spark TTS",
            model_type=ModelType.TTS,
            is_open=False,
            is_active=False, # API stopped working
            model_url="https://github.com/SparkAudio/Spark-TTS",
        ),
        Model(
            id="playht-2.0",
            name="PlayHT 2.0",
            model_type=ModelType.TTS,
            is_open=False,
            is_active=False,
            model_url="https://play.ht/",
        ),
        Model(
            id="styletts2",
            name="StyleTTS 2",
            model_type=ModelType.TTS,
            is_open=False,
            model_url="https://github.com/yl4579/StyleTTS2",
        ),
        Model(
            id="kokoro-v1",
            name="Kokoro v1.0",
            model_type=ModelType.TTS,
            is_open=True,
            model_url="https://huggingface.co/hexgrad/Kokoro-82M",
        ),
        Model(
            id="cosyvoice-2.0",
            name="CosyVoice 2.0",
            model_type=ModelType.TTS,
            is_open=True,
            model_url="https://github.com/FunAudioLLM/CosyVoice",
        ),
        Model(
            id="papla-p1",
            name="Papla P1",
            model_type=ModelType.TTS,
            is_open=False,
            model_url="https://papla.media/",
        ),
        Model(
            id="hume-octave",
            name="Hume Octave",
            model_type=ModelType.TTS,
            is_open=False,
            model_url="https://hume.ai/",
        ),
        Model(
            id="megatts3",
            name="MegaTTS 3",
            model_type=ModelType.TTS,
            is_active=False,
            is_open=True,
            model_url="https://github.com/bytedance/MegaTTS3",
        ),
        Model(
            id="minimax-02-hd",
            name="MiniMax Speech-02-HD",
            model_type=ModelType.TTS,
            is_open=False,
            model_url="http://minimax.io/",
        ),
        Model(
            id="minimax-02-turbo",
            name="MiniMax Speech-02-Turbo",
            model_type=ModelType.TTS,
            is_open=False,
            model_url="http://minimax.io/",
        ),
        Model(
            id="lanternfish-1",
            name="OpenAudio S1",
            model_type=ModelType.TTS,
            is_open=False,
            is_active=False, # NOTE: Waiting to receive a pool of voices
            model_url="https://fish.audio/",
        ),
        Model(
            id="async-1",
            name="CastleFlow v1.0",
            model_type=ModelType.TTS,
            is_open=False,
            is_active=False,
            model_url="https://async.ai/",
        ),
    ]
    conversational_models = [
        Model(
            id="csm-1b",
            name="CSM 1B",
            model_type=ModelType.CONVERSATIONAL,
            is_open=True,
            model_url="https://huggingface.co/sesame/csm-1b",
        ),
        Model(
            id="playdialog-1.0",
            name="PlayDialog 1.0",
            model_type=ModelType.CONVERSATIONAL,
            is_open=False,
            model_url="https://play.ht/",
        ),
        Model(
            id="dia-1.6b",
            name="Dia 1.6B",
            model_type=ModelType.CONVERSATIONAL,
            is_open=True,
            model_url="https://huggingface.co/nari-labs/Dia-1.6B",
        ),
    ]

    all_models = tts_models + conversational_models

    for model in all_models:
        existing = Model.query.filter_by(
            id=model.id, model_type=model.model_type
        ).first()
        if not existing:
            db.session.add(model)
        else:
            # Update model attributes if they've changed, but preserve other data
            existing.name = model.name
            existing.is_open = model.is_open
            if model.is_active is not None:
                existing.is_active = model.is_active

    db.session.commit()


def get_top_voters(limit=10):
    """
    Get the top voters by number of votes.
    
    Args:
        limit (int): Number of users to return
        
    Returns:
        list: List of dictionaries containing user data and vote counts
    """
    # Query users who have opted in to the leaderboard and have at least one vote
    top_users = db.session.query(
        User, func.count(Vote.id).label('vote_count')
    ).join(Vote).filter(
        User.show_in_leaderboard == True
    ).group_by(User.id).order_by(
        func.count(Vote.id).desc()
    ).limit(limit).all()
    
    result = []
    for i, (user, vote_count) in enumerate(top_users, 1):
        result.append({
            "rank": i,
            "username": user.username,
            "vote_count": vote_count,
            "join_date": user.join_date.strftime("%b %d, %Y")
        })
    
    return result


def toggle_user_leaderboard_visibility(user_id):
    """Toggle user's leaderboard visibility setting"""
    user = User.query.get(user_id)
    if not user:
        return None
    
    user.show_in_leaderboard = not user.show_in_leaderboard
    db.session.commit()
    return user.show_in_leaderboard


def check_user_timeout(user_id):
    """Check if a user is currently timed out"""
    if not user_id:
        return False, None
    
    active_timeout = UserTimeout.query.filter_by(
        user_id=user_id, 
        is_active=True
    ).filter(
        UserTimeout.expires_at > datetime.utcnow()
    ).order_by(UserTimeout.expires_at.desc()).first()
    
    return active_timeout is not None, active_timeout


def create_user_timeout(user_id, reason, timeout_type, duration_days, created_by=None, related_campaign_id=None):
    """Create a new user timeout"""
    expires_at = datetime.utcnow() + timedelta(days=duration_days)
    
    timeout = UserTimeout(
        user_id=user_id,
        reason=reason,
        timeout_type=timeout_type,
        expires_at=expires_at,
        created_by=created_by,
        related_campaign_id=related_campaign_id
    )
    
    db.session.add(timeout)
    db.session.commit()
    return timeout


def cancel_user_timeout(timeout_id, cancelled_by, cancel_reason):
    """Cancel an active timeout"""
    timeout = UserTimeout.query.get(timeout_id)
    if not timeout:
        return False, "Timeout not found"
    
    timeout.is_active = False
    timeout.cancelled_at = datetime.utcnow()
    timeout.cancelled_by = cancelled_by
    timeout.cancel_reason = cancel_reason
    
    db.session.commit()
    return True, "Timeout cancelled successfully"


def log_coordinated_campaign(model_id, model_type, vote_count, user_count, 
                           time_window_hours, confidence_score, participants_data):
    """Log a detected coordinated voting campaign"""
    campaign = CoordinatedVotingCampaign(
        model_id=model_id,
        model_type=model_type,
        time_window_hours=time_window_hours,
        vote_count=vote_count,
        user_count=user_count,
        confidence_score=confidence_score
    )
    
    db.session.add(campaign)
    db.session.flush()  # Get campaign ID
    
    # Add participants
    for participant_data in participants_data:
        participant = CampaignParticipant(
            campaign_id=campaign.id,
            user_id=participant_data['user_id'],
            votes_in_campaign=participant_data['votes_in_campaign'],
            first_vote_at=participant_data['first_vote_at'],
            last_vote_at=participant_data['last_vote_at'],
            suspicion_level=participant_data['suspicion_level']
        )
        db.session.add(participant)
    
    db.session.commit()
    return campaign


def get_user_timeouts(user_id=None, active_only=True, limit=50):
    """Get user timeouts with optional filtering"""
    query = UserTimeout.query
    
    if user_id:
        query = query.filter_by(user_id=user_id)
    
    if active_only:
        query = query.filter_by(is_active=True).filter(
            UserTimeout.expires_at > datetime.utcnow()
        )
    
    return query.order_by(UserTimeout.created_at.desc()).limit(limit).all()


def get_coordinated_campaigns(status=None, limit=50):
    """Get coordinated voting campaigns with optional status filtering"""
    query = CoordinatedVotingCampaign.query
    
    if status:
        query = query.filter_by(status=status)
    
    return query.order_by(CoordinatedVotingCampaign.detected_at.desc()).limit(limit).all()


def resolve_campaign(campaign_id, resolved_by, status, admin_notes=None):
    """Mark a campaign as resolved"""
    campaign = CoordinatedVotingCampaign.query.get(campaign_id)
    if not campaign:
        return False, "Campaign not found"
    
    campaign.status = status
    campaign.resolved_by = resolved_by
    campaign.resolved_at = datetime.utcnow()
    if admin_notes:
        campaign.admin_notes = admin_notes
    
    db.session.commit()
    return True, "Campaign resolved successfully"


def hash_sentence(sentence_text):
    """Generate a SHA-256 hash for a sentence"""
    return hashlib.sha256(sentence_text.strip().encode('utf-8')).hexdigest()


def is_sentence_consumed(sentence_text):
    """Check if a sentence has already been consumed"""
    sentence_hash = hash_sentence(sentence_text)
    return ConsumedSentence.query.filter_by(sentence_hash=sentence_hash).first() is not None


def mark_sentence_consumed(sentence_text, session_id=None, usage_type='direct'):
    """Mark a sentence as consumed"""
    sentence_hash = hash_sentence(sentence_text)
    
    # Check if already consumed
    existing = ConsumedSentence.query.filter_by(sentence_hash=sentence_hash).first()
    if existing:
        return existing  # Already consumed
    
    consumed_sentence = ConsumedSentence(
        sentence_hash=sentence_hash,
        sentence_text=sentence_text,
        session_id=session_id,
        usage_type=usage_type
    )
    
    db.session.add(consumed_sentence)
    db.session.commit()
    return consumed_sentence


def get_unconsumed_sentences(sentence_pool):
    """Filter a list of sentences to only include unconsumed ones"""
    if not sentence_pool:
        return []
    
    # Get all consumed sentence hashes
    consumed_hashes = set(
        row[0] for row in db.session.query(ConsumedSentence.sentence_hash).all()
    )
    
    # Filter out consumed sentences
    unconsumed = []
    for sentence in sentence_pool:
        if hash_sentence(sentence) not in consumed_hashes:
            unconsumed.append(sentence)
    
    return unconsumed


def get_consumed_sentences_count():
    """Get the total count of consumed sentences"""
    return ConsumedSentence.query.count()


def get_random_unconsumed_sentence(sentence_pool):
    """Get a random unconsumed sentence from the pool"""
    unconsumed = get_unconsumed_sentences(sentence_pool)
    if not unconsumed:
        return None
    
    import random
    return random.choice(unconsumed)
