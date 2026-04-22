"""
Tests for tools/scraperapi_normalizer.py — parsing only, no network calls.
"""
import json
from pathlib import Path

import pytest

from tools.scraperapi_normalizer import normalize, normalize_all, _parse_beds, _parse_baths, _parse_sqft, _parse_lot_sqft, _parse_hoa, _parse_dom

FIXTURE = Path(__file__).parent / "fixtures" / "scraperapi_response.json"


@pytest.fixture
def sample_response():
    return json.loads(FIXTURE.read_text())


@pytest.fixture
def first_listing(sample_response):
    return sample_response["listing"][0]


# ── normalize_all ──────────────────────────────────────────────────────────────

def test_normalize_all_returns_all_listings(sample_response):
    listings = normalize_all(sample_response)
    assert len(listings) == 4


def test_normalize_all_accepts_bare_list(sample_response):
    listings = normalize_all(sample_response["listing"])
    assert len(listings) == 4


# ── address + zpid ────────────────────────────────────────────────────────────

def test_address_preserved(first_listing):
    listing = normalize(first_listing)
    assert listing.address == "1234 Pine St, Seattle, WA 98101"


def test_zpid_derived_from_url(first_listing):
    listing = normalize(first_listing)
    assert listing.zpid == "12345678"


def test_missing_address_returns_none():
    assert normalize({"number_beds": "3 beds", "price": [{"cost": 500000}]}) is None


# ── price ──────────────────────────────────────────────────────────────────────

def test_price_parsed(first_listing):
    listing = normalize(first_listing)
    assert listing.price == 750000.0


def test_price_second_listing(sample_response):
    listing = normalize(sample_response["listing"][1])
    assert listing.price == 1250000.0


# ── beds / baths ──────────────────────────────────────────────────────────────

def test_beds_parsed(first_listing):
    listing = normalize(first_listing)
    assert listing.beds == 3


def test_baths_parsed(first_listing):
    listing = normalize(first_listing)
    assert listing.baths == 2.0


def test_baths_half(sample_response):
    listing = normalize(sample_response["listing"][1])
    assert listing.baths == 2.5


def test_studio_parsed_as_zero():
    assert _parse_beds("Studio") == 0


def test_beds_none_on_empty():
    assert _parse_beds("") is None


# ── sqft ──────────────────────────────────────────────────────────────────────

def test_sqft_parsed(first_listing):
    listing = normalize(first_listing)
    assert listing.sqft == 1450


def test_sqft_with_commas():
    assert _parse_sqft("2,100 sq ft") == 2100


def test_sqft_none_on_empty():
    assert _parse_sqft("") is None


# ── lot_sqft ──────────────────────────────────────────────────────────────────

def test_lot_sqft_parsed(first_listing):
    listing = normalize(first_listing)
    assert listing.lot_sqft == 4160


def test_lot_sqft_none_when_missing():
    listing = normalize({
        "address": "1 Main St, Seattle, WA 98101",
        "price": [{"cost": 500000}],
        "key_facts": ["2 garage spots"],
        "badge": [],
    })
    assert listing.lot_sqft is None


def test_parse_lot_sqft_various_formats():
    assert _parse_lot_sqft(["6,000 sq ft lot"]) == 6000
    assert _parse_lot_sqft(["5500 SQ FT LOT"]) == 5500
    assert _parse_lot_sqft(["No lot info"]) is None


# ── HOA ───────────────────────────────────────────────────────────────────────

def test_hoa_parsed(first_listing):
    listing = normalize(first_listing)
    assert listing.hoa_fee == 250.0


def test_hoa_zero_treated_as_none():
    # "$0 HOA" should produce 0.0 — the screen stage handles None vs 0 distinction
    assert _parse_hoa(["$0 HOA"]) == 0.0


def test_hoa_none_when_absent():
    assert _parse_hoa(["4,160 sq ft lot", "2 garage spots"]) is None


# ── DOM ───────────────────────────────────────────────────────────────────────

def test_dom_days_on_redfin():
    assert _parse_dom(["5 DAYS ON REDFIN"]) == 5


def test_dom_new_hours_ago():
    assert _parse_dom(["NEW 3 HRS AGO"]) == 0


def test_dom_new_plain():
    assert _parse_dom(["NEW"]) == 0


def test_dom_new_day_ago():
    assert _parse_dom(["NEW 1 DAY AGO"]) == 1


def test_dom_price_drop_is_none():
    assert _parse_dom(["PRICE DROP"]) is None


def test_dom_none_when_no_badge():
    listing = normalize(sample_response["listing"][0] if False else {
        "address": "1 Main St, Seattle, WA 98101",
        "price": [{"cost": 500000}],
        "key_facts": [],
        "badge": [],
    })
    assert listing.days_on_market is None


# ── listing_url ───────────────────────────────────────────────────────────────

def test_listing_url_preserved(first_listing):
    listing = normalize(first_listing)
    assert listing.listing_url == "https://www.redfin.com/WA/Seattle/1234-Pine-St-98101/home/12345678"


def test_listing_url_none_when_absent():
    listing = normalize({
        "address": "1 Main St, Seattle, WA 98101",
        "price": [{"cost": 500000}],
        "key_facts": [],
        "badge": [],
    })
    assert listing.listing_url is None


# ── lat/lon ───────────────────────────────────────────────────────────────────

def test_no_latlon_from_scraperapi(first_listing):
    """ScraperAPI doesn't return coordinates — geocoding resolves them later."""
    listing = normalize(first_listing)
    assert listing.latitude is None
    assert listing.longitude is None