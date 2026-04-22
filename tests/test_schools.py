"""
Tests for tools/schools.py — NCES + WA OSPI school enrichment.

Both external API calls are mocked with pytest-httpx.
Functions degrade gracefully — failures return empty lists or None.
"""
import re

import pytest
from pytest_httpx import HTTPXMock

from tools.models import SchoolInfo
from tools.schools import _haversine, _pct_to_float, enrich_with_proficiency, fetch_nearby_schools

_NCES_URL = re.compile(r"https://nces\.ed\.gov/opengis")
_OSPI_URL = re.compile(r"https://data\.wa\.gov/resource/u25x-vdun")

_NCES_RESPONSE = {
    "features": [
        {
            "attributes": {
                "NCESSCH": 530006000015,
                "NAME": "Test Elementary School",
                "LAT": 47.55,
                "LON": -122.28,
            }
        },
        {
            "attributes": {
                "NCESSCH": 530006000016,
                "NAME": "Test High School",
                "LAT": 47.56,
                "LON": -122.29,
            }
        },
    ]
}

_OSPI_RESPONSE = [
    {"proficiency_ela_rate": "65.0%", "proficiency_math_rate": "70.0%"}
]


# ── fetch_nearby_schools ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_fetch_nearby_schools_success(httpx_mock: HTTPXMock):
    httpx_mock.add_response(url=_NCES_URL, json=_NCES_RESPONSE)
    schools = await fetch_nearby_schools(47.5518, -122.281)
    assert len(schools) == 2
    assert schools[0].name == "Test Elementary School"
    assert schools[0].level == "Elementary"
    assert schools[0].nces_id == "530006000015"
    assert schools[0].distance_miles is not None


@pytest.mark.asyncio
async def test_fetch_nearby_schools_api_failure(httpx_mock: HTTPXMock):
    httpx_mock.add_response(url=_NCES_URL, status_code=500)
    schools = await fetch_nearby_schools(47.5518, -122.281)
    assert schools == []


@pytest.mark.asyncio
async def test_fetch_nearby_schools_empty_response(httpx_mock: HTTPXMock):
    httpx_mock.add_response(url=_NCES_URL, json={"features": []})
    schools = await fetch_nearby_schools(47.5518, -122.281)
    assert schools == []


@pytest.mark.asyncio
async def test_fetch_nearby_schools_sorted_by_distance(httpx_mock: HTTPXMock):
    httpx_mock.add_response(url=_NCES_URL, json=_NCES_RESPONSE)
    schools = await fetch_nearby_schools(47.5518, -122.281)
    distances = [s.distance_miles for s in schools if s.distance_miles is not None]
    assert distances == sorted(distances)


# ── enrich_with_proficiency ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_enrich_with_proficiency_success(httpx_mock: HTTPXMock):
    httpx_mock.add_response(url=_OSPI_URL, json=_OSPI_RESPONSE)
    schools = [SchoolInfo(nces_id="530006000015", name="Test Elementary Unique1", level="Elementary")]
    result = await enrich_with_proficiency(schools)
    assert len(result) == 1
    assert result[0].proficiency_score == pytest.approx(67.5)  # avg(65, 70)


@pytest.mark.asyncio
async def test_enrich_with_proficiency_no_data(httpx_mock: HTTPXMock):
    httpx_mock.add_response(url=_OSPI_URL, json=[])
    schools = [SchoolInfo(nces_id="000000000000", name="Unknown School Unique2", level="School")]
    result = await enrich_with_proficiency(schools)
    assert result[0].proficiency_score is None


@pytest.mark.asyncio
async def test_enrich_with_proficiency_api_failure(httpx_mock: HTTPXMock):
    httpx_mock.add_response(url=_OSPI_URL, status_code=503)
    schools = [SchoolInfo(nces_id="999999999999", name="Failing School Unique3", level="High")]
    result = await enrich_with_proficiency(schools)
    assert result[0].proficiency_score is None


@pytest.mark.asyncio
async def test_enrich_with_proficiency_empty_list():
    result = await enrich_with_proficiency([])
    assert result == []


@pytest.mark.asyncio
async def test_enrich_with_proficiency_ela_only(httpx_mock: HTTPXMock):
    """When only ELA rate is present, proficiency = ELA rate."""
    httpx_mock.add_response(url=_OSPI_URL, json=[{"proficiency_ela_rate": "55.0%"}])
    schools = [SchoolInfo(nces_id="111111111111", name="ELA Only School Unique4", level="Elementary")]
    result = await enrich_with_proficiency(schools)
    assert result[0].proficiency_score == pytest.approx(55.0)


# ── _pct_to_float ──────────────────────────────────────────────────────────────

def test_pct_to_float_normal():
    assert _pct_to_float("46.30%") == pytest.approx(46.3)

def test_pct_to_float_none():
    assert _pct_to_float(None) is None

def test_pct_to_float_empty():
    assert _pct_to_float("") is None

def test_pct_to_float_no_percent_sign():
    assert _pct_to_float("70.0") == pytest.approx(70.0)


# ── _haversine ─────────────────────────────────────────────────────────────────

def test_haversine_same_point():
    assert _haversine(47.55, -122.28, 47.55, -122.28) == pytest.approx(0.0, abs=0.001)


def test_haversine_known_distance():
    # Seattle to Bellevue is ~7 miles
    dist = _haversine(47.6062, -122.3321, 47.6101, -122.2015)
    assert 5 < dist < 9