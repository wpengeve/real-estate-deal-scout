"""
Tests for where deployed state lives — the database and generated reports.

Both used to be hardcoded relative paths. On a host with ephemeral disk that
means every redeploy wipes user accounts, sessions, and every shared report URL.
Each is now overridable, and each override is read lazily so it cannot depend on
whether load_dotenv() has already run.
"""
import importlib
from pathlib import Path

import pytest

import app as app_module
import db as db_module


@pytest.fixture(autouse=True)
def _reset_engine():
    """The engine is cached process-wide; rebuild it around every test."""
    db_module.reset_engine()
    yield
    db_module.reset_engine()


# ── database ──────────────────────────────────────────────────────────────────

def test_db_path_defaults_to_data_dir(monkeypatch):
    monkeypatch.delenv("SCOUT_DB_PATH", raising=False)
    assert db_module.db_path() == Path("data/scout.db")


def test_scout_db_path_overrides_default(monkeypatch, tmp_path):
    target = tmp_path / "volume" / "scout.db"
    monkeypatch.setenv("SCOUT_DB_PATH", str(target))
    assert db_module.db_path() == target


def test_db_path_is_read_lazily(monkeypatch, tmp_path):
    """
    Set after import — as load_dotenv() does — and it must still take effect.
    Reading at import time is how an override silently does nothing.
    """
    importlib.reload(db_module)
    target = tmp_path / "late" / "scout.db"
    monkeypatch.setenv("SCOUT_DB_PATH", str(target))
    assert db_module.db_path() == target


def test_init_db_writes_to_the_override(monkeypatch, tmp_path):
    """The end that matters: tables land on the volume, not the default path."""
    target = tmp_path / "volume" / "scout.db"
    monkeypatch.setenv("SCOUT_DB_PATH", str(target))
    db_module.reset_engine()

    db_module.init_db()

    assert target.exists()
    assert str(target) in str(db_module.get_engine().url)


def test_init_db_creates_missing_parent_directories(monkeypatch, tmp_path):
    """A freshly mounted volume can be empty — nested paths must still work."""
    target = tmp_path / "a" / "b" / "c" / "scout.db"
    monkeypatch.setenv("SCOUT_DB_PATH", str(target))
    db_module.reset_engine()

    db_module.init_db()

    assert target.exists()


def test_accounts_persist_across_engine_rebuilds(monkeypatch, tmp_path):
    """
    A redeploy restarts the process. With the file on a volume the user must
    still be there afterwards — that is the whole point of the override.
    """
    monkeypatch.setenv("SCOUT_DB_PATH", str(tmp_path / "scout.db"))
    db_module.reset_engine()
    db_module.init_db()

    session = db_module.get_db()
    user = db_module.get_or_create_user(session, "someone@example.com")
    user_id = user.id
    session.commit()
    session.close()

    db_module.reset_engine()  # stand-in for a process restart
    db_module.init_db()

    session = db_module.get_db()
    again = db_module.get_or_create_user(session, "someone@example.com")
    assert again.id == user_id
    session.close()


# ── reports ───────────────────────────────────────────────────────────────────

def test_outputs_dir_defaults_to_outputs(monkeypatch):
    monkeypatch.delenv("SCOUT_OUTPUTS_DIR", raising=False)
    assert app_module.outputs_dir() == Path("outputs")


def test_scout_outputs_dir_overrides_default(monkeypatch, tmp_path):
    monkeypatch.setenv("SCOUT_OUTPUTS_DIR", str(tmp_path))
    assert app_module.outputs_dir() == tmp_path


def test_outputs_dir_is_read_lazily(monkeypatch, tmp_path):
    """Same trap as the database: an import-time read ignores the override."""
    monkeypatch.setenv("SCOUT_OUTPUTS_DIR", str(tmp_path / "late"))
    assert app_module.outputs_dir() == tmp_path / "late"


def test_report_path_lands_in_the_override(monkeypatch, tmp_path):
    monkeypatch.setenv("SCOUT_OUTPUTS_DIR", str(tmp_path / "vol"))
    path = app_module._report_path("abc123")

    assert path == tmp_path / "vol" / "web_abc123.html"
    assert path.parent.is_dir()  # created, so the write won't fail


def test_report_path_creates_nested_directories(monkeypatch, tmp_path):
    """
    mkdir(exist_ok=True) without parents=True would raise here — an empty
    mounted volume with a nested reports path is exactly the deploy case.
    """
    monkeypatch.setenv("SCOUT_OUTPUTS_DIR", str(tmp_path / "x" / "y" / "z"))
    path = app_module._report_path("run1")
    assert path.parent.is_dir()


def test_pruning_reads_the_override(monkeypatch, tmp_path):
    """
    Startup pruning globs the reports directory. Pointed at the default while
    reports were written to a volume, it would silently prune nothing.
    """
    import time

    reports = tmp_path / "vol"
    reports.mkdir()
    old = reports / "web_old.html"
    old.write_text("stale", encoding="utf-8")
    ancient = time.time() - 60 * 86400
    import os as _os
    _os.utime(old, (ancient, ancient))

    monkeypatch.setenv("SCOUT_OUTPUTS_DIR", str(reports))
    app_module._prune_outputs(max_age_days=7)

    assert not old.exists()


def test_pruning_keeps_the_run_log(monkeypatch, tmp_path):
    import os as _os
    import time

    reports = tmp_path / "vol"
    reports.mkdir()
    log = reports / "run_log.jsonl"
    log.write_text("{}", encoding="utf-8")
    ancient = time.time() - 60 * 86400
    _os.utime(log, (ancient, ancient))

    monkeypatch.setenv("SCOUT_OUTPUTS_DIR", str(reports))
    app_module._prune_outputs(max_age_days=7)

    assert log.exists()
