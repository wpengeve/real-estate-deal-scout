"""
Tests for db.py — SQLite models and CRUD helpers.

Uses an in-memory SQLite database so tests are fast and isolated.
"""
import pytest
from datetime import datetime, timedelta, timezone
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from db import (
    Base,
    AuthToken,
    ReportRun,
    User,
    UserSession,
    create_auth_token,
    create_session,
    delete_session,
    get_or_create_user,
    get_session_user,
    get_user_runs,
    upsert_report_run,
    verify_auth_token,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def db():
    """In-memory SQLite session, rolled back after each test."""
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session = Session(engine)
    yield session
    session.close()


@pytest.fixture
def user(db):
    return get_or_create_user(db, "test@example.com")


# ── get_or_create_user ────────────────────────────────────────────────────────

def test_create_user(db):
    u = get_or_create_user(db, "alice@example.com")
    assert u.id is not None
    assert u.email == "alice@example.com"


def test_create_user_lowercases_email(db):
    u = get_or_create_user(db, "ALICE@Example.COM")
    assert u.email == "alice@example.com"


def test_create_user_strips_whitespace(db):
    u = get_or_create_user(db, "  alice@example.com  ")
    assert u.email == "alice@example.com"


def test_get_existing_user(db):
    u1 = get_or_create_user(db, "alice@example.com")
    u2 = get_or_create_user(db, "alice@example.com")
    assert u1.id == u2.id


def test_two_different_users(db):
    u1 = get_or_create_user(db, "alice@example.com")
    u2 = get_or_create_user(db, "bob@example.com")
    assert u1.id != u2.id


# ── create_auth_token / verify_auth_token ─────────────────────────────────────

def test_create_auth_token(db, user):
    token = create_auth_token(db, user)
    assert token.id is not None
    assert len(token.token) == 32  # uuid4 hex
    assert token.used is False
    assert token.expires_at > datetime.now()


def test_verify_auth_token_succeeds(db, user):
    token = create_auth_token(db, user)
    result = verify_auth_token(db, token.token)
    assert result is not None
    assert result.id == user.id


def test_verify_auth_token_marks_used(db, user):
    token = create_auth_token(db, user)
    verify_auth_token(db, token.token)
    db.refresh(token)
    assert token.used is True


def test_verify_auth_token_cannot_reuse(db, user):
    token = create_auth_token(db, user)
    verify_auth_token(db, token.token)
    result = verify_auth_token(db, token.token)
    assert result is None


def test_verify_auth_token_expired(db, user):
    token = create_auth_token(db, user, ttl_minutes=-1)  # already expired
    result = verify_auth_token(db, token.token)
    assert result is None


def test_verify_auth_token_wrong_token(db):
    result = verify_auth_token(db, "nonexistent-token")
    assert result is None


# ── create_session / get_session_user / delete_session ───────────────────────

def test_create_session(db, user):
    session = create_session(db, user)
    assert session.id is not None
    assert session.user_id == user.id
    assert session.expires_at > datetime.now()


def test_get_session_user_succeeds(db, user):
    session = create_session(db, user)
    found = get_session_user(db, session.id)
    assert found is not None
    assert found.id == user.id


def test_get_session_user_not_found(db):
    result = get_session_user(db, "bogus-session-id")
    assert result is None


def test_get_session_user_expired(db, user):
    session = UserSession(
        id="expired-session",
        user_id=user.id,
        expires_at=datetime.now(timezone.utc) - timedelta(days=1),
    )
    db.add(session)
    db.commit()
    result = get_session_user(db, "expired-session")
    assert result is None


def test_delete_session(db, user):
    session = create_session(db, user)
    sid = session.id
    delete_session(db, sid)
    result = get_session_user(db, sid)
    assert result is None


def test_delete_nonexistent_session_is_safe(db):
    delete_session(db, "does-not-exist")  # should not raise


# ── upsert_report_run / get_user_runs ─────────────────────────────────────────

def test_upsert_report_run_creates(db, user):
    run = upsert_report_run(db, "abc123", user_id=user.id, market="Seattle, WA", status="running")
    assert run.id is not None
    assert run.run_id == "abc123"
    assert run.status == "running"
    assert run.market == "Seattle, WA"


def test_upsert_report_run_updates_status(db, user):
    upsert_report_run(db, "abc123", user_id=user.id, status="running")
    run = upsert_report_run(db, "abc123", status="done", deals_found=5)
    assert run.status == "done"
    assert run.deals_found == 5


def test_upsert_report_run_anonymous(db):
    run = upsert_report_run(db, "anon-run", user_id=None, status="done")
    assert run.user_id is None


def test_upsert_report_run_stores_criteria(db):
    criteria = {"market_name": "Seattle", "max_price": 1000000}
    run = upsert_report_run(db, "crit-run", criteria=criteria, status="running")
    import json
    assert json.loads(run.criteria_summary) == criteria


def test_get_user_runs_returns_done_only(db, user):
    upsert_report_run(db, "run-done", user_id=user.id, status="done", market="Seattle")
    upsert_report_run(db, "run-error", user_id=user.id, status="error", market="Seattle")
    upsert_report_run(db, "run-running", user_id=user.id, status="running", market="Seattle")
    runs = get_user_runs(db, user.id)
    assert len(runs) == 1
    assert runs[0].run_id == "run-done"


def test_get_user_runs_only_own_runs(db):
    u1 = get_or_create_user(db, "u1@example.com")
    u2 = get_or_create_user(db, "u2@example.com")
    upsert_report_run(db, "run-u1", user_id=u1.id, status="done")
    upsert_report_run(db, "run-u2", user_id=u2.id, status="done")
    runs = get_user_runs(db, u1.id)
    assert len(runs) == 1
    assert runs[0].run_id == "run-u1"


def test_get_user_runs_empty(db, user):
    runs = get_user_runs(db, user.id)
    assert runs == []


def test_get_user_runs_limit(db, user):
    for i in range(25):
        upsert_report_run(db, f"run-{i}", user_id=user.id, status="done")
    runs = get_user_runs(db, user.id, limit=10)
    assert len(runs) == 10