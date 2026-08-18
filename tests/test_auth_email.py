"""
Tests for the magic-link login path now that it actually sends email.

The module that talks to Resend is covered in test_email_sender.py; this file
covers the wiring — that a real email is attempted, that a failed or
unconfigured send still lets you in through the console, and that a successful
send does *not* leave the live link in the server's logs.
"""
import threading

import pytest
from fastapi.testclient import TestClient

import app as app_module
import db as db_module


@pytest.fixture
def client(monkeypatch, tmp_path):
    """A live app on a throwaway database, never touching data/scout.db."""
    monkeypatch.setenv("SCOUT_DB_PATH", str(tmp_path / "scout.db"))
    monkeypatch.setenv("SCOUT_OUTPUTS_DIR", str(tmp_path / "outputs"))
    monkeypatch.setenv("BASE_URL", "https://dealscout.example")
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    db_module.reset_engine()
    with TestClient(app_module.app) as c:
        yield c
    db_module.reset_engine()


@pytest.fixture
def sends(monkeypatch):
    """Stub the provider; records every (to, link) it was asked to deliver."""
    calls = []

    def _fake(to, link):
        calls.append({"to": to, "link": link, "thread": threading.current_thread()})
        return True

    monkeypatch.setattr(app_module, "send_magic_link", _fake)
    return calls


# ── the send is actually attempted ────────────────────────────────────────────

def test_requesting_a_link_sends_an_email(client, sends):
    res = client.post("/api/auth/request", json={"email": "buyer@example.com"})

    assert res.status_code == 200
    assert len(sends) == 1
    assert sends[0]["to"] == "buyer@example.com"


def test_email_carries_a_link_built_from_base_url(client, sends):
    """
    BASE_URL is what makes the link reachable from someone else's inbox. A link
    pointing at localhost is delivered and useless.
    """
    client.post("/api/auth/request", json={"email": "buyer@example.com"})

    assert sends[0]["link"].startswith("https://dealscout.example/auth/verify?token=")


def test_address_is_normalised_before_sending(client, sends):
    client.post("/api/auth/request", json={"email": "  Buyer@Example.COM "})
    assert sends[0]["to"] == "buyer@example.com"


def test_send_runs_off_the_event_loop(client, sends):
    """
    The provider call is blocking with a 10s timeout. On the event loop it would
    stall every other request in the process for that long.
    """
    client.post("/api/auth/request", json={"email": "buyer@example.com"})
    assert sends[0]["thread"] is not threading.main_thread()


def test_emailed_link_actually_logs_you_in(client, sends):
    """End to end: the token in the delivered link is one the app will accept."""
    client.post("/api/auth/request", json={"email": "buyer@example.com"})
    link = sends[0]["link"]

    res = client.get(link.replace("https://dealscout.example", ""), follow_redirects=False)

    assert res.status_code == 302
    assert client.cookies.get("scout_session")


# ── the console fallback ──────────────────────────────────────────────────────

def test_unconfigured_provider_falls_back_to_the_console(client, capsys):
    """No RESEND_API_KEY — the real module returns False and you still get in."""
    res = client.post("/api/auth/request", json={"email": "buyer@example.com"})

    assert res.status_code == 200
    assert "console" in res.json()["message"]
    assert "/auth/verify?token=" in capsys.readouterr().out


def test_failed_delivery_still_returns_200(client, monkeypatch, capsys):
    """
    A provider outage must not take login down — the console link is the way in,
    so the request has to succeed even though nothing was delivered.
    """
    monkeypatch.setattr(app_module, "send_magic_link", lambda to, link: False)

    res = client.post("/api/auth/request", json={"email": "buyer@example.com"})

    assert res.status_code == 200
    assert "/auth/verify?token=" in capsys.readouterr().out


def test_successful_send_keeps_the_link_out_of_the_logs(client, sends, capsys):
    """
    A live login link in a host's logs is a credential for anyone with log
    access. Print it only when it's the only way in.
    """
    res = client.post("/api/auth/request", json={"email": "buyer@example.com"})

    assert "/auth/verify?token=" not in capsys.readouterr().out
    assert "console" not in res.json()["message"]


def test_invalid_address_is_rejected_before_any_send(client, sends):
    res = client.post("/api/auth/request", json={"email": "not-an-email"})

    assert res.status_code == 422
    assert sends == []


# ── what the sign-in box tells you ────────────────────────────────────────────

def test_login_box_mentions_the_console_when_unconfigured(client):
    assert "server console" in client.get("/").text


def test_login_box_drops_the_console_note_once_email_works(client, monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY", "re_test_key")
    assert "server console" not in client.get("/").text
