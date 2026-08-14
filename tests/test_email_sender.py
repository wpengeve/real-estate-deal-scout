"""
Tests for tools/email_sender.py — magic-link delivery.

No network: the provider is stubbed with pytest-httpx, matching the convention
in test_solar.py / test_schools.py.

The behaviour that matters most here is what happens when sending *fails*. A
login attempt must never 500 because an email provider is unconfigured, slow, or
rejecting — it has to fall back to the console link and say so.
"""
import json

import httpx
import pytest
from pytest_httpx import HTTPXMock

from tools import email_sender
from tools.email_sender import is_configured, render_magic_link_email, send_magic_link

_API_URL = "https://api.resend.com/emails"
_LINK = "https://dealscout.app/auth/verify?token=abc123"


@pytest.fixture
def with_key(monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY", "re_test_key")
    monkeypatch.delenv("EMAIL_FROM", raising=False)


# ── configuration ─────────────────────────────────────────────────────────────

def test_not_configured_without_key(monkeypatch):
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    assert is_configured() is False


def test_configured_with_key(with_key):
    assert is_configured() is True


# ── rendering ─────────────────────────────────────────────────────────────────

def test_both_parts_carry_the_link():
    html_body, text_body = render_magic_link_email(_LINK)
    assert _LINK in html_body
    assert _LINK in text_body


def test_html_repeats_the_raw_link_outside_the_button():
    """
    Buttons fail to render in some clients. The bare URL is the fallback, so it
    must appear twice — once as the href, once as visible text.
    """
    html_body, _ = render_magic_link_email(_LINK)
    assert html_body.count(_LINK) >= 2


def test_expiry_is_stated_in_both_parts():
    html_body, text_body = render_magic_link_email(_LINK)
    assert "15 minutes" in html_body
    assert "15 minutes" in text_body


def test_link_is_escaped_into_the_href():
    """
    BASE_URL is operator input, so a quote in it would otherwise break out of
    the href attribute and into the markup.
    """
    html_body, _ = render_magic_link_email('https://x.test/verify?t=a"onmouseover=b')
    assert '"onmouseover=b' not in html_body
    assert "&quot;onmouseover=b" in html_body


def test_recipient_is_escaped_when_shown():
    html_body, _ = render_magic_link_email(_LINK, "<script>@evil.test")
    assert "<script>@evil.test" not in html_body
    assert "&lt;script&gt;@evil.test" in html_body


def test_recipient_line_omitted_when_not_supplied():
    html_body, _ = render_magic_link_email(_LINK)
    assert "Requested for" not in html_body


# ── sending ───────────────────────────────────────────────────────────────────

def test_no_key_skips_the_send(monkeypatch):
    """Local development: no key, no request, console fallback."""
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    assert send_magic_link("someone@example.com", _LINK) is False


def test_successful_send(httpx_mock: HTTPXMock, with_key):
    httpx_mock.add_response(url=_API_URL, json={"id": "msg_1"})
    assert send_magic_link("someone@example.com", _LINK) is True


def test_payload_carries_both_parts_and_recipient(httpx_mock: HTTPXMock, with_key):
    httpx_mock.add_response(url=_API_URL, json={"id": "msg_1"})
    send_magic_link("someone@example.com", _LINK)

    sent = json.loads(httpx_mock.get_requests()[0].content)
    assert sent["to"] == ["someone@example.com"]
    assert sent["subject"] == "Your Deal Scout sign-in link"
    assert _LINK in sent["html"]
    assert _LINK in sent["text"]  # the text alternative must actually be sent


def test_api_key_is_sent_as_a_bearer_token(httpx_mock: HTTPXMock, with_key):
    httpx_mock.add_response(url=_API_URL, json={"id": "msg_1"})
    send_magic_link("someone@example.com", _LINK)
    assert httpx_mock.get_requests()[0].headers["Authorization"] == "Bearer re_test_key"


def test_email_from_overrides_the_default_sender(httpx_mock: HTTPXMock, monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY", "re_test_key")
    monkeypatch.setenv("EMAIL_FROM", "Scout <hello@mydomain.test>")
    httpx_mock.add_response(url=_API_URL, json={"id": "msg_1"})

    send_magic_link("someone@example.com", _LINK)

    sent = json.loads(httpx_mock.get_requests()[0].content)
    assert sent["from"] == "Scout <hello@mydomain.test>"


# ── failure paths — none of these may raise ───────────────────────────────────

def test_rejected_send_returns_false(httpx_mock: HTTPXMock, with_key):
    """An unverified domain or bad key returns 4xx — log it, don't crash login."""
    httpx_mock.add_response(url=_API_URL, status_code=403, json={"message": "domain not verified"})
    assert send_magic_link("someone@example.com", _LINK) is False


def test_server_error_returns_false(httpx_mock: HTTPXMock, with_key):
    httpx_mock.add_response(url=_API_URL, status_code=500, text="upstream boom")
    assert send_magic_link("someone@example.com", _LINK) is False


def test_network_failure_returns_false(httpx_mock: HTTPXMock, with_key):
    httpx_mock.add_exception(httpx.ConnectError("dns failure"))
    assert send_magic_link("someone@example.com", _LINK) is False


def test_timeout_returns_false(httpx_mock: HTTPXMock, with_key):
    """A hung provider must not hold a login request open indefinitely."""
    httpx_mock.add_exception(httpx.ReadTimeout("too slow"))
    assert send_magic_link("someone@example.com", _LINK) is False


def test_request_sets_a_timeout(httpx_mock: HTTPXMock, with_key):
    """Every external call gets a timeout — no unbounded waits (CLAUDE.md)."""
    assert email_sender._TIMEOUT is not None
    assert 0 < email_sender._TIMEOUT <= 30
