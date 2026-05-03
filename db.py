"""
SQLite database — models, engine, and CRUD helpers.

Schema:
    users           — registered users (email + created_at)
    auth_tokens     — one-use magic link tokens (15-min expiry)
    user_sessions   — session cookies (30-day expiry)
    report_runs     — every pipeline run, optionally linked to a user
"""
import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, relationship

_DB_PATH = Path("data/scout.db")
_DB_URL = f"sqlite:///{_DB_PATH}"

# ── Models ────────────────────────────────────────────────────────────────────

class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    email = Column(String, unique=True, nullable=False, index=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    sessions = relationship("UserSession", back_populates="user", cascade="all, delete-orphan")
    auth_tokens = relationship("AuthToken", back_populates="user", cascade="all, delete-orphan")
    runs = relationship("ReportRun", back_populates="user")


class AuthToken(Base):
    __tablename__ = "auth_tokens"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    token = Column(String, unique=True, nullable=False, index=True)
    expires_at = Column(DateTime, nullable=False)
    used = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    user = relationship("User", back_populates="auth_tokens")


class UserSession(Base):
    __tablename__ = "user_sessions"

    id = Column(String, primary_key=True)  # UUID hex
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    expires_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    user = relationship("User", back_populates="sessions")


class ChatSessionRecord(Base):
    __tablename__ = "chat_sessions"

    session_id = Column(String, primary_key=True)
    messages_json = Column(Text, nullable=False, default="[]")
    extracted_json = Column(Text, nullable=True)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class ReportRun(Base):
    __tablename__ = "report_runs"

    id = Column(Integer, primary_key=True)
    run_id = Column(String, unique=True, nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)  # null = anonymous
    market = Column(String, nullable=True)
    status = Column(String, default="running", nullable=False)  # running | done | error
    deals_found = Column(Integer, nullable=True)
    criteria_summary = Column(Text, nullable=True)  # JSON — what was searched
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    user = relationship("User", back_populates="runs")


# ── Engine ────────────────────────────────────────────────────────────────────

engine = create_engine(
    _DB_URL,
    connect_args={"check_same_thread": False},  # required for SQLite + threading
)


def init_db() -> None:
    """Create all tables. Safe to call on every startup."""
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(engine)


def get_db() -> Session:
    """Return a new SQLAlchemy session. Caller is responsible for closing."""
    return Session(engine)


# ── CRUD helpers ──────────────────────────────────────────────────────────────

def get_or_create_user(db: Session, email: str) -> User:
    """Return existing user or create a new one."""
    user = db.query(User).filter(User.email == email.lower().strip()).first()
    if not user:
        user = User(email=email.lower().strip())
        db.add(user)
        db.commit()
        db.refresh(user)
    return user


def create_auth_token(db: Session, user: User, ttl_minutes: int = 15) -> AuthToken:
    """Create a one-use magic link token that expires in ttl_minutes."""
    token = AuthToken(
        user_id=user.id,
        token=uuid.uuid4().hex,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=ttl_minutes),
    )
    db.add(token)
    db.commit()
    db.refresh(token)
    return token


def verify_auth_token(db: Session, token_str: str) -> User | None:
    """
    Validate a magic link token. Marks it used and returns the User, or None if
    the token is invalid, expired, or already used.
    """
    record = db.query(AuthToken).filter(AuthToken.token == token_str).first()
    if not record:
        return None
    if record.used:
        return None
    if record.expires_at.replace(tzinfo=timezone.utc) < datetime.now(timezone.utc):
        return None
    record.used = True
    db.commit()
    return record.user


def create_session(db: Session, user: User, ttl_days: int = 30) -> UserSession:
    """Create a new session cookie record."""
    session = UserSession(
        id=uuid.uuid4().hex,
        user_id=user.id,
        expires_at=datetime.now(timezone.utc) + timedelta(days=ttl_days),
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


def get_session_user(db: Session, session_id: str) -> User | None:
    """Look up a user by session cookie ID. Returns None if expired or not found."""
    record = db.query(UserSession).filter(UserSession.id == session_id).first()
    if not record:
        return None
    if record.expires_at.replace(tzinfo=timezone.utc) < datetime.now(timezone.utc):
        return None
    return record.user


def delete_session(db: Session, session_id: str) -> None:
    """Delete a session (logout)."""
    db.query(UserSession).filter(UserSession.id == session_id).delete()
    db.commit()


def upsert_report_run(
    db: Session,
    run_id: str,
    *,
    user_id: int | None = None,
    market: str | None = None,
    status: str = "running",
    deals_found: int | None = None,
    criteria: dict | None = None,
) -> ReportRun:
    """Create or update a ReportRun record."""
    record = db.query(ReportRun).filter(ReportRun.run_id == run_id).first()
    if not record:
        record = ReportRun(
            run_id=run_id,
            user_id=user_id,
            market=market,
            criteria_summary=json.dumps(criteria) if criteria else None,
        )
        db.add(record)

    record.status = status
    if deals_found is not None:
        record.deals_found = deals_found
    db.commit()
    db.refresh(record)
    return record


def get_user_runs(db: Session, user_id: int, limit: int = 20) -> list[ReportRun]:
    """Return the most recent report runs for a user."""
    return (
        db.query(ReportRun)
        .filter(ReportRun.user_id == user_id, ReportRun.status == "done")
        .order_by(ReportRun.created_at.desc())
        .limit(limit)
        .all()
    )


# ── Chat session persistence ───────────────────────────────────────────────────

def _serialize_messages(messages: list[dict]) -> list[dict]:
    """Convert Anthropic SDK content blocks to plain dicts for JSON storage."""
    result = []
    for msg in messages:
        content = msg["content"]
        if isinstance(content, list):
            serialized: list[dict] = []
            for block in content:
                if hasattr(block, "model_dump"):
                    serialized.append(block.model_dump())
                elif isinstance(block, dict):
                    serialized.append(block)
                else:
                    serialized.append({"type": "text", "text": str(block)})
            result.append({"role": msg["role"], "content": serialized})
        else:
            result.append(msg)
    return result


def save_chat_session(
    db: Session,
    session_id: str,
    messages: list[dict],
    extracted: dict | None,
) -> None:
    """Upsert chat session state."""
    serialized = _serialize_messages(messages)
    record = db.query(ChatSessionRecord).filter(
        ChatSessionRecord.session_id == session_id
    ).first()
    now = datetime.now(timezone.utc)
    if record:
        record.messages_json = json.dumps(serialized)
        record.extracted_json = json.dumps(extracted) if extracted else None
        record.updated_at = now
    else:
        db.add(ChatSessionRecord(
            session_id=session_id,
            messages_json=json.dumps(serialized),
            extracted_json=json.dumps(extracted) if extracted else None,
            updated_at=now,
        ))
    db.commit()


def load_chat_session(
    db: Session, session_id: str
) -> tuple[list[dict], dict | None] | None:
    """Load chat session state. Returns (messages, extracted) or None if not found."""
    record = db.query(ChatSessionRecord).filter(
        ChatSessionRecord.session_id == session_id
    ).first()
    if not record:
        return None
    messages = json.loads(record.messages_json)
    extracted = json.loads(record.extracted_json) if record.extracted_json else None
    return messages, extracted


def delete_chat_session(db: Session, session_id: str) -> None:
    """Delete a chat session record."""
    db.query(ChatSessionRecord).filter(
        ChatSessionRecord.session_id == session_id
    ).delete()
    db.commit()