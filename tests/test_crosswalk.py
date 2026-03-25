"""
Tests for tools/crosswalk.py — USPS ZIP to county name resolution.

Both the HUD USPS crosswalk and Census Bureau calls are mocked with pytest-httpx.
"""
import re

import pytest
from pytest_httpx import HTTPXMock

import tools.crosswalk as crosswalk_module
from tools.crosswalk import zip_to_county

_HUD_URL = re.compile(r"https://www\.huduser\.gov/hudapi/public/usps")
_CENSUS_URL = re.compile(r"https://api\.census\.gov/data/2020/dec/pl")

_HUD_KEY = "test-hud-key"


@pytest.fixture(autouse=True)
def reset_cache():
    """Clear the in-process ZIP cache between tests."""
    crosswalk_module._cache.clear()
    yield
    crosswalk_module._cache.clear()


def _hud_response(geoid: str, res_ratio: float = 0.9) -> dict:
    return {"data": {"results": [{"geoid": geoid, "res_ratio": res_ratio}]}}


def _census_response(county_name: str, state: str, county: str) -> list:
    return [["NAME", "state", "county"], [county_name, state, county]]


# ── Happy path ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_zip_to_county_returns_state_and_county(monkeypatch, httpx_mock: HTTPXMock):
    monkeypatch.setattr(crosswalk_module, "_HUD_API_KEY", _HUD_KEY)
    httpx_mock.add_response(url=_HUD_URL, json=_hud_response("53033"))
    httpx_mock.add_response(url=_CENSUS_URL, json=_census_response("King County, Washington", "53", "033"))

    result = await zip_to_county("98118")
    assert result == ("53", "King County")


@pytest.mark.asyncio
async def test_zip_to_county_strips_state_from_county_name(monkeypatch, httpx_mock: HTTPXMock):
    """Census returns 'Pierce County, Washington' — we strip ', Washington'."""
    monkeypatch.setattr(crosswalk_module, "_HUD_API_KEY", _HUD_KEY)
    httpx_mock.add_response(url=_HUD_URL, json=_hud_response("53053"))
    httpx_mock.add_response(url=_CENSUS_URL, json=_census_response("Pierce County, Washington", "53", "053"))

    result = await zip_to_county("98402")
    assert result == ("53", "Pierce County")


# ── Caching ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_zip_to_county_caches_result(monkeypatch, httpx_mock: HTTPXMock):
    """Second call for the same ZIP must not make additional HTTP requests."""
    monkeypatch.setattr(crosswalk_module, "_HUD_API_KEY", _HUD_KEY)
    httpx_mock.add_response(url=_HUD_URL, json=_hud_response("53033"))
    httpx_mock.add_response(url=_CENSUS_URL, json=_census_response("King County, Washington", "53", "033"))

    first = await zip_to_county("98118")
    second = await zip_to_county("98118")   # should use cache, no new HTTP calls
    assert first == second == ("53", "King County")


@pytest.mark.asyncio
async def test_zip_to_county_caches_none_on_failure(monkeypatch, httpx_mock: HTTPXMock):
    """A failed lookup is also cached so we don't hammer the API repeatedly."""
    monkeypatch.setattr(crosswalk_module, "_HUD_API_KEY", _HUD_KEY)
    httpx_mock.add_response(url=_HUD_URL, status_code=500)

    first = await zip_to_county("00000")
    second = await zip_to_county("00000")   # uses cache
    assert first is None
    assert second is None


# ── No API key ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_zip_to_county_returns_none_without_api_key(monkeypatch):
    monkeypatch.setattr(crosswalk_module, "_HUD_API_KEY", None)
    result = await zip_to_county("98118")
    assert result is None


# ── HUD API failures ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_zip_to_county_returns_none_on_hud_error(monkeypatch, httpx_mock: HTTPXMock):
    monkeypatch.setattr(crosswalk_module, "_HUD_API_KEY", _HUD_KEY)
    httpx_mock.add_response(url=_HUD_URL, status_code=401)
    result = await zip_to_county("98118")
    assert result is None


@pytest.mark.asyncio
async def test_zip_to_county_returns_none_when_hud_results_empty(monkeypatch, httpx_mock: HTTPXMock):
    monkeypatch.setattr(crosswalk_module, "_HUD_API_KEY", _HUD_KEY)
    httpx_mock.add_response(url=_HUD_URL, json={"data": {"results": []}})
    result = await zip_to_county("00000")
    assert result is None


@pytest.mark.asyncio
async def test_zip_to_county_picks_highest_res_ratio(monkeypatch, httpx_mock: HTTPXMock):
    """When a ZIP spans multiple counties, pick the one with the highest residential ratio."""
    monkeypatch.setattr(crosswalk_module, "_HUD_API_KEY", _HUD_KEY)
    multi = {"data": {"results": [
        {"geoid": "53033", "res_ratio": 0.30},
        {"geoid": "53061", "res_ratio": 0.70},
    ]}}
    httpx_mock.add_response(url=_HUD_URL, json=multi)
    httpx_mock.add_response(url=_CENSUS_URL, json=_census_response("Snohomish County, Washington", "53", "061"))

    result = await zip_to_county("98201")
    assert result == ("53", "Snohomish County")


# ── Census API failures ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_zip_to_county_returns_none_on_census_error(monkeypatch, httpx_mock: HTTPXMock):
    monkeypatch.setattr(crosswalk_module, "_HUD_API_KEY", _HUD_KEY)
    httpx_mock.add_response(url=_HUD_URL, json=_hud_response("53033"))
    httpx_mock.add_response(url=_CENSUS_URL, status_code=500)
    result = await zip_to_county("98118")
    assert result is None


@pytest.mark.asyncio
async def test_zip_to_county_returns_none_when_census_empty(monkeypatch, httpx_mock: HTTPXMock):
    monkeypatch.setattr(crosswalk_module, "_HUD_API_KEY", _HUD_KEY)
    httpx_mock.add_response(url=_HUD_URL, json=_hud_response("53033"))
    httpx_mock.add_response(url=_CENSUS_URL, json=[["NAME", "state", "county"]])  # header only, no data row
    result = await zip_to_county("98118")
    assert result is None
