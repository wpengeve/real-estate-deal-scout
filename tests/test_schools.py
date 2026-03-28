"""
Tests for tools/schools.py — NCES + Urban Institute school enrichment.

Both external API calls are mocked with pytest-httpx.
Functions degrade gracefully — failures return empty lists or None.
"""
import re

import pytest
from pytest_httpx import HTTPXMock

from tools.models import SchoolInfo
from tools.schools import _haversine, enrich_with_proficiency, fetch_nearby_schools

_NCES_URL = re.compile(r"https://nces\.ed\.gov/opengis")
_URBAN_URL = re.compile(r"https://educationdata\.urban\.org")

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

_URBAN_RESPONSE = {
    "results": [
        {"math_test_pct_prof_midpt": 70.0, "read_test_pct_prof_midpt": 65.0}
    ]
}


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
    httpx_mock.add_response(url=_URBAN_URL, json=_URBAN_RESPONSE)
    schools = [SchoolInfo(nces_id="530006000015", name="Test Elementary", level="Elementary")]
    result = await enrich_with_proficiency(schools)
    assert len(result) == 1
    assert result[0].proficiency_score == 67.5  # avg(70, 65)


@pytest.mark.asyncio
async def test_enrich_with_proficiency_no_data(httpx_mock: HTTPXMock):
    httpx_mock.add_response(url=_URBAN_URL, json={"results": []})
    httpx_mock.add_response(url=_URBAN_URL, json={"results": []})
    httpx_mock.add_response(url=_URBAN_URL, json={"results": []})
    schools = [SchoolInfo(nces_id="000000000000", name="Unknown School", level="School")]
    result = await enrich_with_proficiency(schools)
    assert result[0].proficiency_score is None


@pytest.mark.asyncio
async def test_enrich_with_proficiency_api_failure(httpx_mock: HTTPXMock):
    # Use a unique nces_id to avoid hitting the in-process cache from other tests
    httpx_mock.add_response(url=_URBAN_URL, status_code=503)
    httpx_mock.add_response(url=_URBAN_URL, status_code=503)
    httpx_mock.add_response(url=_URBAN_URL, status_code=503)
    schools = [SchoolInfo(nces_id="999999999999", name="Test School", level="High")]
    result = await enrich_with_proficiency(schools)
    assert result[0].proficiency_score is None


@pytest.mark.asyncio
async def test_enrich_with_proficiency_empty_list():
    result = await enrich_with_proficiency([])
    assert result == []


# ── _haversine ─────────────────────────────────────────────────────────────────

def test_haversine_same_point():
    assert _haversine(47.55, -122.28, 47.55, -122.28) == pytest.approx(0.0, abs=0.001)


def test_haversine_known_distance():
    # Seattle to Bellevue is ~7 miles
    dist = _haversine(47.6062, -122.3321, 47.6101, -122.2015)
    assert 5 < dist < 9
