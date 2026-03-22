"""
Tests for tools/screen.py — listing screening logic.

All tests are pure unit tests with no I/O.
"""
import pytest

from tools.models import RawListing, ScreeningCriteria
from tools.screen import screen_all, screen_listing

CRITERIA = ScreeningCriteria(max_price=500_000, min_beds=3, max_dom=30)


def _listing(**kwargs) -> RawListing:
    defaults = dict(
        zpid="test-1",
        address="123 Main St, Austin TX",
        price=400_000,
        beds=3,
        days_on_market=15,
    )
    defaults.update(kwargs)
    return RawListing(**defaults)


# ── Passing ────────────────────────────────────────────────────────────────────

def test_screen_passes_when_all_criteria_met():
    result = screen_listing(_listing(), CRITERIA)
    assert result.passed is True
    assert result.reason is None


def test_screen_passes_at_exact_max_price():
    result = screen_listing(_listing(price=500_000), CRITERIA)
    assert result.passed is True


def test_screen_passes_at_exact_min_beds():
    result = screen_listing(_listing(beds=3), CRITERIA)
    assert result.passed is True


def test_screen_passes_at_exact_max_dom():
    result = screen_listing(_listing(days_on_market=30), CRITERIA)
    assert result.passed is True


def test_screen_passes_dom_zero_just_listed():
    """DOM=0 (just listed) should pass — it is not a risk flag at screening stage."""
    result = screen_listing(_listing(days_on_market=0), CRITERIA)
    assert result.passed is True


# ── Filtering ──────────────────────────────────────────────────────────────────

def test_screen_filters_price_too_high():
    result = screen_listing(_listing(price=500_001), CRITERIA)
    assert result.passed is False
    assert result.reason == "price_too_high"


def test_screen_filters_beds_below_min():
    result = screen_listing(_listing(beds=2), CRITERIA)
    assert result.passed is False
    assert result.reason == "beds_below_min"


def test_screen_filters_dom_exceeded():
    result = screen_listing(_listing(days_on_market=31), CRITERIA)
    assert result.passed is False
    assert result.reason == "dom_exceeded"


# ── None fields ────────────────────────────────────────────────────────────────

def test_screen_filters_price_none():
    result = screen_listing(_listing(price=None), CRITERIA)
    assert result.passed is False
    assert result.reason == "price_unknown"


def test_screen_filters_beds_none():
    result = screen_listing(_listing(beds=None), CRITERIA)
    assert result.passed is False
    assert result.reason == "beds_unknown"


def test_screen_filters_dom_none():
    result = screen_listing(_listing(days_on_market=None), CRITERIA)
    assert result.passed is False
    assert result.reason == "dom_unknown"


# ── screen_all ─────────────────────────────────────────────────────────────────

def test_screen_all_returns_passed_and_filtered():
    listings = [
        _listing(zpid="a", price=400_000),    # passes
        _listing(zpid="b", price=600_000),    # filtered
        _listing(zpid="c", beds=2),            # filtered
    ]
    passed, filtered = screen_all(listings, CRITERIA)
    assert len(passed) == 1
    assert passed[0].zpid == "a"
    assert len(filtered) == 2
    assert {f["reason"] for f in filtered} == {"price_too_high", "beds_below_min"}


def test_screen_all_empty_input():
    passed, filtered = screen_all([], CRITERIA)
    assert passed == []
    assert filtered == []


def test_screen_all_none_pass():
    """All listings fail screening — pipeline handles this gracefully."""
    listings = [_listing(zpid="a", price=999_999)]
    passed, filtered = screen_all(listings, CRITERIA)
    assert passed == []
    assert len(filtered) == 1
