"""
Tests for tools/enrich.py — Walk Score enrichment.

WalkScore API calls are mocked with pytest-httpx.
The enrich functions never raise — they degrade gracefully.
"""
import re
from unittest.mock import patch

import pytest
import httpx
from pytest_httpx import HTTPXMock

from tools.models import RawListing
from tools.enrich import enrich_neighborhood, enrich_all

_WALKSCORE_KEY = "test-key"
_WALKSCORE_URL = re.compile(r"https://api\.walkscore\.com")

def _listing(walk_score=None, lat=30.25, lon=-97.75, **kwargs) -> RawListing:
    return RawListing(
        zpid="test-1",
        address="123 Main St, Seattle WA 98101",
        price=400_000,
        beds=3,
        days_on_market=15,
        estimated_monthly_rent=2500,
        walk_score=walk_score,
        latitude=lat,
        longitude=lon,
        nearby_schools=[],  # pre-set to skip NCES lookup in enrich tests
        **kwargs,
    )


# ── Uses fixture walk_score when available ─────────────────────────────────────

@pytest.mark.asyncio
async def test_enrich_uses_fixture_walk_score(monkeypatch):
    """If listing.walk_score is already set, no API call is made."""
    monkeypatch.setenv("WALKSCORE_API_KEY", _WALKSCORE_KEY)
    result = await enrich_neighborhood(_listing(walk_score=74))
    assert result.walk_score == 74


# ── No API key ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_enrich_returns_none_walk_score_without_api_key(monkeypatch):
    monkeypatch.delenv("WALKSCORE_API_KEY", raising=False)
    result = await enrich_neighborhood(_listing(walk_score=None))
    assert result.walk_score is None
    assert result.error is None  # graceful, not an error


# ── Successful API response ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_enrich_walk_score_success(monkeypatch, httpx_mock: HTTPXMock):
    monkeypatch.setattr("tools.enrich._WALKSCORE_API_KEY", _WALKSCORE_KEY)
    httpx_mock.add_response(
        url=_WALKSCORE_URL,
        json={"walkscore": 68, "description": "Somewhat Walkable"},
        status_code=200,
    )
    result = await enrich_neighborhood(_listing(walk_score=None))
    assert result.walk_score == 68


# ── Rate limit → retry → None ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_enrich_walk_score_rate_limited(monkeypatch, httpx_mock: HTTPXMock):
    monkeypatch.setattr("tools.enrich._WALKSCORE_API_KEY", _WALKSCORE_KEY)
    # 3 attempts total: initial + 2 retries (range(1, _WALKSCORE_RETRIES + 2))
    for _ in range(3):
        httpx_mock.add_response(url=_WALKSCORE_URL, status_code=429)
    with patch("tools.enrich.asyncio.sleep"):  # skip backoff sleep in tests
        result = await enrich_neighborhood(_listing(walk_score=None))
    assert result.walk_score is None


# ── Timeout → None ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_enrich_walk_score_timeout(monkeypatch, httpx_mock: HTTPXMock):
    monkeypatch.setattr("tools.enrich._WALKSCORE_API_KEY", _WALKSCORE_KEY)
    httpx_mock.add_exception(
        httpx.TimeoutException("timeout"),
        url=_WALKSCORE_URL,
    )
    result = await enrich_neighborhood(_listing(walk_score=None))
    assert result.walk_score is None


# ── No coordinates → None ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_enrich_no_coordinates_returns_none(monkeypatch):
    monkeypatch.setenv("WALKSCORE_API_KEY", _WALKSCORE_KEY)
    result = await enrich_neighborhood(_listing(lat=None, lon=None, walk_score=None))
    assert result.walk_score is None


# ── Rent comps passthrough ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_enrich_preserves_estimated_monthly_rent():
    result = await enrich_neighborhood(_listing(walk_score=55))
    assert result.estimated_monthly_rent == 2500


# ── enrich_all ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_enrich_all_returns_result_for_each_listing():
    listings = [_listing(walk_score=50), _listing(walk_score=60)]
    results = await enrich_all(listings)
    assert len(results) == 2
    assert results[0].walk_score == 50
    assert results[1].walk_score == 60


@pytest.mark.asyncio
async def test_enrich_all_empty_input():
    results = await enrich_all([])
    assert results == []
