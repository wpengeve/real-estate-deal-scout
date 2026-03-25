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


# ── CSV backend tests ──────────────────────────────────────────────────────────

def test_fetch_listings_csv_reads_file(tmp_path):
    csv_file = tmp_path / "redfin.csv"
    csv_file.write_text(_SAMPLE_CSV, encoding="utf-8")
    config = FetchConfig(data_source="csv", csv_path=str(csv_file))
    listings = fetch_listings("Seattle, WA", config)
    assert len(listings) == 2
    assert listings[0].home_type == "Single Family"


def test_fetch_listings_csv_raises_when_file_missing(tmp_path):
    config = FetchConfig(data_source="csv", csv_path=str(tmp_path / "missing.csv"))
    with pytest.raises(FileNotFoundError, match="CSV file not found"):
        fetch_listings("Seattle, WA", config)


def test_fetch_listings_csv_handles_utf8_bom(tmp_path):
    """Redfin sometimes exports CSV with a UTF-8 BOM — should parse cleanly."""
    csv_file = tmp_path / "redfin.csv"
    csv_file.write_bytes(b"\xef\xbb\xbf" + _SAMPLE_CSV.encode("utf-8"))
    config = FetchConfig(data_source="csv", csv_path=str(csv_file))
    listings = fetch_listings("Seattle, WA", config)
    assert len(listings) == 2


# ── Multi-CSV tests ────────────────────────────────────────────────────────────

_SAMPLE_CSV_B = """\
ADDRESS,CITY,STATE OR PROVINCE,ZIP OR POSTAL CODE,PRICE,BEDS,BATHS,SQUARE FEET,LOT SIZE,PROPERTY TYPE,HOA/MONTH,DAYS ON MARKET,LATITUDE,LONGITUDE,URL (SEE https://www.redfin.com/buy-a-home/comparative-market-analysis for info on how Redfin calculates its Comparative Market Analysis)
9999 New Ave,Tacoma,WA,98401,"350,000",3,2.0,"1,200","5,000",Single Family Residential,,5,47.2529,-122.4443,https://www.redfin.com/WA/Tacoma/9999-New-Ave-98401/home/99999
4521 Rainier Ave S,Seattle,WA,98118,"285,000",3,2.0,"1,380","4,200",Single Family Residential,,8,47.5518,-122.2810,https://www.redfin.com/WA/Seattle/4521-Rainier-Ave-S-98118/home/12345
"""


def test_multi_csv_merges_unique_listings(tmp_path):
    f1 = tmp_path / "a.csv"
    f2 = tmp_path / "b.csv"
    f1.write_text(_SAMPLE_CSV, encoding="utf-8")
    f2.write_text(_SAMPLE_CSV_B, encoding="utf-8")
    config = FetchConfig(data_source="csv", csv_paths=[str(f1), str(f2)])
    listings = fetch_listings("Seattle, WA", config)
    # f1 has 2, f2 has 2 but 1 is a duplicate → 3 unique total
    assert len(listings) == 3


def test_multi_csv_deduplicates_by_address(tmp_path):
    f1 = tmp_path / "a.csv"
    f2 = tmp_path / "b.csv"
    f1.write_text(_SAMPLE_CSV, encoding="utf-8")
    f2.write_text(_SAMPLE_CSV, encoding="utf-8")   # identical file
    config = FetchConfig(data_source="csv", csv_paths=[str(f1), str(f2)])
    listings = fetch_listings("Seattle, WA", config)
    assert len(listings) == 2  # no duplicates


def test_multi_csv_first_occurrence_wins(tmp_path):
    """When same address appears in two files, the first file's price is kept."""
    f1 = tmp_path / "a.csv"
    f2 = tmp_path / "b.csv"
    f1.write_text(_SAMPLE_CSV, encoding="utf-8")     # price 285,000
    f2.write_text(_SAMPLE_CSV_B, encoding="utf-8")   # same address, same price in B
    config = FetchConfig(data_source="csv", csv_paths=[str(f1), str(f2)])
    listings = fetch_listings("Seattle, WA", config)
    rainier = next(l for l in listings if "Rainier" in l.address)
    assert rainier.price == 285_000


def test_multi_csv_raises_if_file_missing(tmp_path):
    f1 = tmp_path / "a.csv"
    f1.write_text(_SAMPLE_CSV, encoding="utf-8")
    config = FetchConfig(data_source="csv", csv_paths=[str(f1), str(tmp_path / "missing.csv")])
    with pytest.raises(FileNotFoundError):
        fetch_listings("Seattle, WA", config)


def test_single_csv_path_still_works_when_csv_paths_empty(tmp_path):
    """csv_paths=[] falls back to csv_path — backwards compatible."""
    f = tmp_path / "redfin.csv"
    f.write_text(_SAMPLE_CSV, encoding="utf-8")
    config = FetchConfig(data_source="csv", csv_path=str(f), csv_paths=[])
    listings = fetch_listings("Seattle, WA", config)
    assert len(listings) == 2
