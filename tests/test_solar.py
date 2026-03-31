"""
Tests for tools/solar.py — NREL Solar Resource API integration.

HTTP calls are mocked with pytest-httpx.
Functions degrade gracefully — failures return None.
"""
import re

import pytest
from pytest_httpx import HTTPXMock

import tools.solar as solar_mod
from tools.solar import fetch_solar_ghi, solar_score

_NREL_URL = re.compile(r"https://developer\.nrel\.gov")

_NREL_RESPONSE = {
    "outputs": {
        "avg_ghi": {
            "annual": 3.87,
            "monthly": [2.4, 3.1, 4.2, 5.0, 5.8, 6.1, 6.4, 5.9, 4.7, 3.2, 2.1, 1.9],
        }
    }
}


@pytest.fixture(autouse=True)
def _patch_api_key(monkeypatch):
    """Ensure NREL_API_KEY is set for all tests in this module."""
    monkeypatch.setenv("NREL_API_KEY", "test-key")


@pytest.fixture(autouse=True)
def _clear_cache():
    """Reset in-process cache between tests."""
    solar_mod._solar_cache.clear()
    yield
    solar_mod._solar_cache.clear()


# ── fetch_solar_ghi ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_fetch_solar_ghi_success(httpx_mock: HTTPXMock):
    httpx_mock.add_response(url=_NREL_URL, json=_NREL_RESPONSE)
    result = await fetch_solar_ghi(47.55, -122.28)
    assert result == pytest.approx(3.87)


@pytest.mark.asyncio
async def test_fetch_solar_ghi_no_api_key(monkeypatch):
    monkeypatch.delenv("NREL_API_KEY", raising=False)
    result = await fetch_solar_ghi(47.55, -122.28)
    assert result is None


@pytest.mark.asyncio
async def test_fetch_solar_ghi_api_failure(httpx_mock: HTTPXMock):
    httpx_mock.add_response(url=_NREL_URL, status_code=500)
    result = await fetch_solar_ghi(47.55, -122.28)
    assert result is None


@pytest.mark.asyncio
async def test_fetch_solar_ghi_empty_outputs(httpx_mock: HTTPXMock):
    httpx_mock.add_response(url=_NREL_URL, json={"outputs": {}})
    result = await fetch_solar_ghi(47.55, -122.28)
    assert result is None


@pytest.mark.asyncio
async def test_fetch_solar_ghi_cached(httpx_mock: HTTPXMock):
    """Second call with same (rounded) coordinates must not make a second HTTP request."""
    httpx_mock.add_response(url=_NREL_URL, json=_NREL_RESPONSE)
    r1 = await fetch_solar_ghi(47.5518, -122.281)
    r2 = await fetch_solar_ghi(47.5521, -122.284)  # rounds to same key
    assert r1 == r2
    # If a second request were made, pytest-httpx would raise (no more responses queued)


@pytest.mark.asyncio
async def test_fetch_solar_ghi_different_coords_not_cached(httpx_mock: HTTPXMock):
    """Coordinates that round differently should each make their own request."""
    httpx_mock.add_response(url=_NREL_URL, json=_NREL_RESPONSE)
    httpx_mock.add_response(url=_NREL_URL, json={**_NREL_RESPONSE, "outputs": {"avg_ghi": {"annual": 4.10}}})
    r1 = await fetch_solar_ghi(47.55, -122.28)
    r2 = await fetch_solar_ghi(47.60, -122.33)
    assert r1 == pytest.approx(3.87)
    assert r2 == pytest.approx(4.10)


# ── solar_score ─────────────────────────────────────────────────────────────────

def test_solar_score_none():
    assert solar_score(None) is None

def test_solar_score_low():
    assert solar_score(3.0) == 1

def test_solar_score_boundary_low():
    assert solar_score(3.5) == 2

def test_solar_score_mid():
    assert solar_score(3.8) == 3

def test_solar_score_high():
    assert solar_score(4.0) == 4

def test_solar_score_very_high():
    assert solar_score(4.1) == 5
    assert solar_score(5.0) == 5