from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from datetime import datetime
import math
from sqlalchemy import func

db = SQLAlchemy()


class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(100), unique=True, nullable=False)
    hf_id = db.Column(db.String(100), unique=True, nullable=False)
    join_date = db.Column(db.DateTime, default=datetime.utcnow)
    votes = db.relationship("Vote", backref="user", lazy=True)

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


def calculate_elo_change(winner_elo, loser_elo, k_factor=32):
    """Calculate Elo rating changes for a match."""
    expected_winner = 1 / (1 + math.pow(10, (loser_elo - winner_elo) / 400))
    expected_loser = 1 / (1 + math.pow(10, (winner_elo - loser_elo) / 400))

    winner_new_elo = winner_elo + k_factor * (1 - expected_winner)
    loser_new_elo = loser_elo + k_factor * (0 - expected_loser)

    return winner_new_elo, loser_new_elo


def record_vote(user_id, text, chosen_model_id, rejected_model_id, model_type):
    """Record a vote and update Elo ratings."""
    # Create the vote
    vote = Vote(
        user_id=user_id,  # Can be None for anonymous votes
        text=text,
        model_chosen=chosen_model_id,
        model_rejected=rejected_model_id,
        model_type=model_type,
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

    Args:
        model_type (str): The model type ('tts' or 'conversational')

    Returns:
        list: List of dictionaries containing model data for the leaderboard
    """
    query = Model.query.filter_by(model_type=model_type)

    # Get models ordered by ELO score
    models = query.order_by(Model.current_elo.desc()).all()

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

    Args:
        user_id (int): The user ID
        model_type (str): The model type ('tts' or 'conversational')

    Returns:
        list: List of dictionaries containing model data for the user's personal leaderboard
    """
    # Get all models of the specified type
    models = Model.query.filter_by(model_type=model_type).all()

    # Get user's votes
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
        elo_entry = EloHistory.query.filter(
            EloHistory.model_id == model.id,
            EloHistory.model_type == model_type,
            EloHistory.timestamp <= target_date
        ).order_by(EloHistory.timestamp.desc()).first()
        
        # Skip models that have no history before the target date
        if not elo_entry:
            continue
        
        # Count wins and matches up to the target date
        match_count = Vote.query.filter(
            db.or_(
                Vote.model_chosen == model.id,
                Vote.model_rejected == model.id
            ),
            Vote.model_type == model_type,
            Vote.vote_date <= target_date
        ).count()
        
        win_count = Vote.query.filter(
            Vote.model_chosen == model.id,
            Vote.model_type == model_type,
            Vote.vote_date <= target_date
        ).count()
        
        # Calculate win rate
        win_rate = (win_count / match_count * 100) if match_count > 0 else 0
        
        # Add to result
        result.append({
            "id": model.id,
            "name": model.name,
            "model_url": model.model_url,
            "win_rate": f"{win_rate:.0f}%",
            "total_votes": match_count,
            "elo": int(elo_entry.elo_score),
            "is_open": model.is_open,
        })
    
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
    first_vote = Vote.query.filter_by(model_type=model_type).order_by(Vote.vote_date.asc()).first()
    last_vote = Vote.query.filter_by(model_type=model_type).order_by(Vote.vote_date.desc()).first()
    
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
            id="playht-2.0",
            name="PlayHT 2.0",
            model_type=ModelType.TTS,
            is_open=False,
            model_url="https://play.ht/",
        ),
        Model(
            id="playht-3.0-mini",
            name="PlayHT 3.0 Mini",
            model_type=ModelType.TTS,
            is_open=False,
            is_active=False,
            model_url="https://play.ht/",
        ),
        Model(
            id="styletts2",
            name="StyleTTS 2",
            model_type=ModelType.TTS,
            is_open=True,
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
