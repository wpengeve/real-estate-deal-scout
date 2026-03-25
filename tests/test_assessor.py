"""
Tests for tools/assessor.py — multi-county parcel lookup.

HTTP calls are mocked with pytest-httpx.
Covers King County (existing), Snohomish County, Pierce County, and routing.
"""
import json
import re

import pytest
from pytest_httpx import HTTPXMock

from tools.assessor import _parse_address, _county_for_zip, lookup_parcel

_KC_URL = re.compile(r"https://gismaps\.kingcounty\.gov")
_SNOCO_URL = re.compile(r"https://gis\.snoco\.org")
_PIERCE_URL = re.compile(r"https://services2\.arcgis\.com.*Tax_Parcels")


# ── _parse_address ─────────────────────────────────────────────────────────────

def test_parse_address_standard():
    result = _parse_address("4521 Rainier Ave S, Seattle, WA 98118")
    assert result == ("4521", "4521 RAINIER AVE", "98118")


def test_parse_address_northeast_suffix():
    result = _parse_address("11234 Roosevelt Way NE, Seattle, WA 98125")
    assert result == ("11234", "11234 ROOSEVELT WAY", "98125")


def test_parse_address_returns_none_missing_zip():
    assert _parse_address("123 Main St, Seattle, WA") is None


def test_parse_address_returns_none_too_short():
    assert _parse_address("123 Main St") is None


def test_parse_address_returns_none_single_word_street():
    assert _parse_address("123, Seattle, WA 98101") is None


# ── _county_for_zip ────────────────────────────────────────────────────────────

def test_county_for_zip_king_980():
    assert _county_for_zip("98056") == "king"


def test_county_for_zip_king_981():
    assert _county_for_zip("98118") == "king"


def test_county_for_zip_snohomish_982():
    assert _county_for_zip("98201") == "snohomish"


def test_county_for_zip_snohomish_982_bothell():
    assert _county_for_zip("98270") == "snohomish"


def test_county_for_zip_pierce_983():
    assert _county_for_zip("98371") == "pierce"


def test_county_for_zip_pierce_984():
    assert _county_for_zip("98402") == "pierce"


def test_county_for_zip_unknown_returns_none():
    assert _county_for_zip("97201") is None  # Portland, OR


def test_county_for_zip_short_returns_none():
    assert _county_for_zip("98") is None


def test_county_for_zip_empty_returns_none():
    assert _county_for_zip("") is None


# ── King County lookup ─────────────────────────────────────────────────────────

def _kc_response(land: float, impr: float, zoning: str) -> str:
    return json.dumps({"features": [{"attributes": {
        "APPRLNDVAL": land,
        "APPR_IMPR": impr,
        "KCA_ZONING": zoning,
    }}]})


@pytest.mark.asyncio
async def test_lookup_parcel_returns_assessed_value_and_zoning(httpx_mock: HTTPXMock):
    httpx_mock.add_response(url=_KC_URL, text=_kc_response(400000, 650000, "NR3"))
    result = await lookup_parcel("4521 Rainier Ave S, Seattle, WA 98118")
    assert result is not None
    assert result["tax_assessed_value"] == 1_050_000.0
    assert result["zoning"] == "NR3"


@pytest.mark.asyncio
async def test_lookup_parcel_returns_none_when_no_match(httpx_mock: HTTPXMock):
    httpx_mock.add_response(url=_KC_URL, text=json.dumps({"features": []}))
    result = await lookup_parcel("4521 Rainier Ave S, Seattle, WA 98118")
    assert result is None


@pytest.mark.asyncio
async def test_lookup_parcel_returns_none_on_http_error(httpx_mock: HTTPXMock):
    httpx_mock.add_response(url=_KC_URL, status_code=500)
    result = await lookup_parcel("4521 Rainier Ave S, Seattle, WA 98118")
    assert result is None


@pytest.mark.asyncio
async def test_lookup_parcel_returns_none_for_unparseable_address():
    result = await lookup_parcel("not a real address")
    assert result is None


@pytest.mark.asyncio
async def test_lookup_parcel_handles_zero_improvement_value(httpx_mock: HTTPXMock):
    """Vacant lot — improvement value is 0, land value still valid."""
    httpx_mock.add_response(url=_KC_URL, text=_kc_response(230000, 0, "NR3"))
    result = await lookup_parcel("4521 Rainier Ave S, Seattle, WA 98118")
    assert result["tax_assessed_value"] == 230_000.0
    assert result["tax_assessed_improvement"] is None


@pytest.mark.asyncio
async def test_lookup_parcel_returns_none_when_both_values_zero(httpx_mock: HTTPXMock):
    """Both land and improvement are 0 — treat as no data."""
    httpx_mock.add_response(url=_KC_URL, text=_kc_response(0, 0, "NR3"))
    result = await lookup_parcel("4521 Rainier Ave S, Seattle, WA 98118")
    assert result["tax_assessed_value"] is None


# ── Snohomish County lookup ────────────────────────────────────────────────────

def _snoco_response(land: int, impr: int, total: int) -> str:
    return json.dumps({"features": [{"attributes": {
        "MKLND": land,
        "MKIMP": impr,
        "MKTTL": total,
    }}]})


@pytest.mark.asyncio
async def test_lookup_parcel_snohomish_returns_values(httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url=_SNOCO_URL,
        text=_snoco_response(250000, 500000, 750000),
    )
    result = await lookup_parcel("1234 Main St, Everett, WA 98201")
    assert result is not None
    assert result["tax_assessed_value"] == 750_000.0
    assert result["tax_assessed_land"] == 250_000.0
    assert result["tax_assessed_improvement"] == 500_000.0
    assert result["zoning"] is None  # Snohomish doesn't expose zoning in this layer


@pytest.mark.asyncio
async def test_lookup_parcel_snohomish_uses_sum_when_total_zero(httpx_mock: HTTPXMock):
    """If MKTTL is 0, fall back to MKLND + MKIMP."""
    httpx_mock.add_response(
        url=_SNOCO_URL,
        text=_snoco_response(200000, 400000, 0),
    )
    result = await lookup_parcel("1234 Main St, Everett, WA 98201")
    assert result["tax_assessed_value"] == 600_000.0


@pytest.mark.asyncio
async def test_lookup_parcel_snohomish_returns_none_on_no_match(httpx_mock: HTTPXMock):
    httpx_mock.add_response(url=_SNOCO_URL, text=json.dumps({"features": []}))
    result = await lookup_parcel("9999 Fake St, Everett, WA 98201")
    assert result is None


@pytest.mark.asyncio
async def test_lookup_parcel_snohomish_returns_none_on_http_error(httpx_mock: HTTPXMock):
    httpx_mock.add_response(url=_SNOCO_URL, status_code=503)
    result = await lookup_parcel("1234 Main St, Everett, WA 98201")
    assert result is None


# ── Pierce County lookup ───────────────────────────────────────────────────────

def _pierce_response(land: int, impr: int) -> str:
    return json.dumps({"features": [{"attributes": {
        "Land_Value": land,
        "Improvement_Value": impr,
    }}]})


@pytest.mark.asyncio
async def test_lookup_parcel_pierce_returns_values(httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url=_PIERCE_URL,
        text=_pierce_response(180000, 320000),
    )
    result = await lookup_parcel("456 Oak Ave, Tacoma, WA 98402")
    assert result is not None
    assert result["tax_assessed_value"] == 500_000.0
    assert result["tax_assessed_land"] == 180_000.0
    assert result["tax_assessed_improvement"] == 320_000.0
    assert result["zoning"] is None  # Pierce doesn't expose zoning in this layer


@pytest.mark.asyncio
async def test_lookup_parcel_pierce_returns_none_on_no_match(httpx_mock: HTTPXMock):
    httpx_mock.add_response(url=_PIERCE_URL, text=json.dumps({"features": []}))
    result = await lookup_parcel("9999 Fake St, Tacoma, WA 98402")
    assert result is None


@pytest.mark.asyncio
async def test_lookup_parcel_pierce_returns_none_on_http_error(httpx_mock: HTTPXMock):
    httpx_mock.add_response(url=_PIERCE_URL, status_code=403)
    result = await lookup_parcel("456 Oak Ave, Tacoma, WA 98402")
    assert result is None


# ── Unknown ZIP routing ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_lookup_parcel_returns_none_for_unknown_zip():
    """Non-WA ZIP — no assessor configured, no HTTP call made."""
    result = await lookup_parcel("123 Main St, Portland, OR 97201")
    assert result is None
