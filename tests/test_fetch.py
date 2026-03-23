"""
Tests for tools/fetch.py — Redfin CSV parsing and data source dispatch.

Redfin HTTP calls are mocked with pytest-httpx.
Fixture loading tests use the real fixtures/listings.json.
"""
import re
from pathlib import Path

import pytest
from pytest_httpx import HTTPXMock

from tools.fetch import _parse_redfin_csv, fetch_listings
from tools.models import FetchConfig

_REDFIN_URL = re.compile(r"https://www\.redfin\.com")

# Minimal Redfin CSV matching the real column headers
_SAMPLE_CSV = """\
ADDRESS,CITY,STATE OR PROVINCE,ZIP OR POSTAL CODE,PRICE,BEDS,BATHS,SQUARE FEET,LOT SIZE,PROPERTY TYPE,HOA/MONTH,DAYS ON MARKET,LATITUDE,LONGITUDE,URL (SEE https://www.redfin.com/buy-a-home/comparative-market-analysis for info on how Redfin calculates its Comparative Market Analysis)
4521 Rainier Ave S,Seattle,WA,98118,"285,000",3,2.0,"1,380","4,200",Single Family Residential,,8,47.5518,-122.2810,https://www.redfin.com/WA/Seattle/4521-Rainier-Ave-S-98118/home/12345
1502 E Pike St,Seattle,WA,98122,"420,000",3,2.0,"1,480",,Condo/Co-op,350.0,18,47.6148,-122.3138,https://www.redfin.com/WA/Seattle/1502-E-Pike-St-98122/home/67890
"""


# ── CSV parsing unit tests ─────────────────────────────────────────────────────

def test_parse_redfin_csv_returns_listings():
    listings = _parse_redfin_csv(_SAMPLE_CSV)
    assert len(listings) == 2


def test_parse_redfin_csv_maps_fields():
    listings = _parse_redfin_csv(_SAMPLE_CSV)
    sfr = listings[0]
    assert sfr.address == "4521 Rainier Ave S, Seattle, WA 98118"
    assert sfr.price == 285_000
    assert sfr.beds == 3
    assert sfr.baths == 2.0
    assert sfr.sqft == 1380
    assert sfr.lot_sqft == 4200
    assert sfr.home_type == "Single Family"
    assert sfr.hoa_fee is None
    assert sfr.days_on_market == 8
    assert sfr.latitude == 47.5518
    assert sfr.longitude == -122.2810


def test_parse_redfin_csv_maps_condo():
    listings = _parse_redfin_csv(_SAMPLE_CSV)
    condo = listings[1]
    assert condo.home_type == "Condo"
    assert condo.hoa_fee == 350.0
    assert condo.lot_sqft is None   # empty in CSV


def test_parse_redfin_csv_uses_url_as_zpid():
    listings = _parse_redfin_csv(_SAMPLE_CSV)
    assert listings[0].zpid == "12345"
    assert listings[1].zpid == "67890"


def test_parse_redfin_csv_skips_malformed_rows():
    bad_csv = "ADDRESS,CITY,STATE OR PROVINCE,ZIP OR POSTAL CODE,PRICE,BEDS,BATHS,SQUARE FEET,LOT SIZE,PROPERTY TYPE,HOA/MONTH,DAYS ON MARKET,LATITUDE,LONGITUDE,URL\n"
    bad_csv += ",,,,not-a-price,,,,,,,,,,\n"
    listings = _parse_redfin_csv(bad_csv)
    # Malformed price → price=None, but listing is still returned (price_unknown handled downstream)
    assert len(listings) == 1
    assert listings[0].price is None


# ── fetch_listings dispatch tests ──────────────────────────────────────────────

def test_fetch_listings_uses_fixtures_by_default():
    listings = fetch_listings("Seattle, WA")
    assert len(listings) == 20


def test_fetch_listings_uses_fixtures_when_config_says_fixtures():
    config = FetchConfig(data_source="fixtures")
    listings = fetch_listings("Seattle, WA", config)
    assert len(listings) == 20


@pytest.mark.asyncio
async def test_fetch_listings_redfin_calls_endpoint(httpx_mock: HTTPXMock):
    httpx_mock.add_response(url=_REDFIN_URL, text=_SAMPLE_CSV, status_code=200)
    config = FetchConfig(data_source="redfin", redfin_region_id=118, redfin_region_type=5)
    listings = fetch_listings("Seattle, WA", config)
    assert len(listings) == 2
    assert listings[0].home_type == "Single Family"


@pytest.mark.asyncio
async def test_fetch_listings_redfin_raises_on_http_error(httpx_mock: HTTPXMock):
    httpx_mock.add_response(url=_REDFIN_URL, status_code=403)
    config = FetchConfig(data_source="redfin")
    with pytest.raises(RuntimeError, match="HTTP 403"):
        fetch_listings("Seattle, WA", config)
