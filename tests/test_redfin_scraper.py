"""
Tests for tools/redfin_scraper.py — HTML parsing only (no network calls).
"""
import pytest
from tools.redfin_scraper import parse, fetch_listing_features

# ── Fixtures ──────────────────────────────────────────────────────────────────

PRIMARY_SUITE_HTML = """
<ul class="bulletList"><li class="propertyDetailsHeader"><span>Room 6 Information</span></li>
<li class="entryItem ">Room Type: Primary Bedroom</li>
<li class="entryItem ">Room Level: Upper</li></ul>
<ul class="bulletList"><li class="propertyDetailsHeader"><span>Bathroom Information</span></li>
<li class="entryItem "># of Full Baths (Total): 2</li></ul>
<script>{\\"ROOMS_6_ROOMS_ROOM_TYPE\\":{\\"amenityGroupTitle\\":\\"Room 6 Information\\",\\"amenityValues\\":[\\"Primary Bedroom\\"]}}</script>
"""

NO_PRIMARY_SUITE_HTML = """
<ul class="bulletList"><li class="propertyDetailsHeader"><span>Room 1 Information</span></li>
<li class="entryItem ">Room Type: Bedroom</li>
<li class="entryItem ">Room Level: Upper</li></ul>
<ul class="bulletList"><li class="propertyDetailsHeader"><span>Bathroom Information</span></li>
<li class="entryItem "># of Full Baths (Total): 1</li></ul>
"""

GARAGE_HTML = """
<li class="entryItem ">Covered Spaces: 2</li>
amenityValues":["Has Attached Garage"]
amenityValues":["Has Garage"]
"""

BASEMENT_FINISHED_HTML = """
<span>Basement Information</span></li>
<li class="entryItem ">Basement Features: Partially Finished</li>
"""

BASEMENT_UNFINISHED_HTML = """
<span>Basement Information</span></li>
<li class="entryItem ">Basement Features: Unfinished</li>
"""

FIREPLACE_HTML = """
<li class="entryItem ">Has Fireplace</li>
<span>Fireplace Information</span>
"""

SITE_FEATURES_HTML = """
<li class="entryItem ">Site Features: Deck, Fenced-Fully, Patio</li>
"""

LOT_FEATURES_HTML = """
<li class="entryItem ">Lot Features: Corner Lot, Curbs, Paved</li>
"""

REMARKS_HTML = """
<div data-rf-test-id="listingRemarks"><p class=""><span>Beautiful home with primary suite and large backyard.</span></p></div>
"""


# ── Primary suite detection ────────────────────────────────────────────────────

def test_detects_primary_suite():
    f = parse(PRIMARY_SUITE_HTML)
    assert f.has_primary_suite is True


def test_no_primary_suite_when_room_section_present_but_no_primary():
    f = parse(NO_PRIMARY_SUITE_HTML)
    assert f.has_primary_suite is False


def test_primary_suite_none_when_no_room_section():
    f = parse("<html><body>Some listing without features section</body></html>")
    assert f.has_primary_suite is None


# ── Garage ────────────────────────────────────────────────────────────────────

def test_detects_garage():
    f = parse(GARAGE_HTML)
    assert f.has_garage is True


def test_garage_spaces():
    f = parse(GARAGE_HTML)
    assert f.garage_spaces == 2


def test_no_garage_when_parking_section_present():
    html = "<span>Parking Information</span><li>Parking Features: Driveway</li>"
    f = parse(html)
    assert f.has_garage is False


def test_garage_none_when_no_parking_section():
    f = parse("<html>no parking info here</html>")
    assert f.has_garage is None


# ── Basement ──────────────────────────────────────────────────────────────────

def test_detects_basement():
    f = parse(BASEMENT_FINISHED_HTML)
    assert f.has_basement is True


def test_basement_finished():
    f = parse(BASEMENT_FINISHED_HTML)
    assert f.basement_finished is True


def test_basement_unfinished():
    f = parse(BASEMENT_UNFINISHED_HTML)
    assert f.basement_finished is False


def test_no_basement_none_when_missing():
    f = parse("<html>no basement info</html>")
    assert f.has_basement is None


# ── Fireplace ─────────────────────────────────────────────────────────────────

def test_detects_fireplace():
    f = parse(FIREPLACE_HTML)
    assert f.has_fireplace is True


def test_no_fireplace_when_section_present():
    html = "<span>Fireplace Information</span><li>No fireplace</li>"
    f = parse(html)
    assert f.has_fireplace is False


# ── Site / lot features ───────────────────────────────────────────────────────

def test_site_features_parsed():
    f = parse(SITE_FEATURES_HTML)
    assert "Deck" in f.site_features
    assert "Fenced-Fully" in f.site_features
    assert "Patio" in f.site_features


def test_lot_features_parsed():
    f = parse(LOT_FEATURES_HTML)
    assert "Corner Lot" in f.lot_features
    assert "Curbs" in f.lot_features


def test_empty_site_features_when_missing():
    f = parse("<html>no site features</html>")
    assert f.site_features == []


# ── Remarks ───────────────────────────────────────────────────────────────────

def test_remarks_extracted():
    f = parse(REMARKS_HTML)
    assert f.remarks is not None
    assert "primary suite" in f.remarks.lower()


def test_remarks_none_when_missing():
    f = parse("<html>no remarks</html>")
    assert f.remarks is None


# ── Walk / Bike / Transit scores ──────────────────────────────────────────────

WALKSCORE_HTML = """
walkScoreInfo{"walkScore":{"value":92},"bikeScore":{"value":75},"transitScore":{"value":68}}
"""

WALKSCORE_PARTIAL_HTML = """
walkScoreInfo{"walkScore":{"value":45}}
"""


def test_walk_score_parsed():
    f = parse(WALKSCORE_HTML)
    assert f.walk_score == 92


def test_bike_score_parsed():
    f = parse(WALKSCORE_HTML)
    assert f.bike_score == 75


def test_transit_score_parsed():
    f = parse(WALKSCORE_HTML)
    assert f.transit_score == 68


def test_walk_score_only_others_none():
    f = parse(WALKSCORE_PARTIAL_HTML)
    assert f.walk_score == 45
    assert f.bike_score is None
    assert f.transit_score is None


def test_scores_none_when_no_walkscore_blob():
    f = parse("<html>no walk score info here</html>")
    assert f.walk_score is None
    assert f.bike_score is None
    assert f.transit_score is None


# ── Year built ─────────────────────────────────────────────────────────────────

YEAR_BUILT_HTML = """
<script type="application/ld+json">{"@type":"SingleFamilyResidence","yearBuilt":1952,"numberOfBedrooms":4}</script>
"""

def test_year_built_parsed():
    f = parse(YEAR_BUILT_HTML)
    assert f.year_built == 1952

def test_year_built_none_when_missing():
    f = parse("<html>no year built here</html>")
    assert f.year_built is None

def test_year_built_not_confused_by_other_numbers():
    # Should not pick up non-year numbers
    f = parse('<html><span>Price: $850,000</span><span>Beds: 4</span></html>')
    assert f.year_built is None


# ── fetch_listing_features: URL guard ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_fetch_returns_none_for_non_redfin_url():
    import httpx
    async with httpx.AsyncClient() as client:
        result = await fetch_listing_features("https://example.com/listing", client)
    assert result is None


@pytest.mark.asyncio
async def test_fetch_returns_none_for_empty_url():
    import httpx
    async with httpx.AsyncClient() as client:
        result = await fetch_listing_features("", client)
    assert result is None