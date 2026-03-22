"""
Tests for tools/risks.py — flood zone and DOM outlier risk flagging.

External API calls (FEMA) are tested via the fixture data path
(flood_zone pre-set on listing), avoiding network calls in tests.
"""
import pytest

from tools.models import (
    AnalyzedListing,
    FinancialResult,
    RawListing,
    RiskLevel,
    ScreeningCriteria,
)
from tools.risks import flag_risks, flag_all

CRITERIA = ScreeningCriteria(max_price=500_000, min_beds=3, max_dom=30, dom_outlier_multiplier=2.0)

GOOD_FINANCIALS = FinancialResult(
    success=True,
    cap_rate=0.062,
    coc_return=0.084,
    monthly_cashflow=312.0,
    noi_annual=17670.0,
    monthly_mortgage=1424.0,
    total_cash_invested=112_000.0,
)


def _analyzed(flood_zone=None, dom=15, **kwargs) -> AnalyzedListing:
    listing = RawListing(
        zpid="test",
        address="123 Main St",
        price=400_000,
        beds=3,
        days_on_market=dom,
        flood_zone=flood_zone,
        **kwargs,
    )
    return AnalyzedListing(
        listing=listing,
        walk_score=55,
        estimated_monthly_rent=2500,
        financials=GOOD_FINANCIALS,
    )


# ── Flood zone ─────────────────────────────────────────────────────────────────

def test_flag_flood_zone_x_no_flag():
    result = flag_risks(_analyzed(flood_zone="X"), CRITERIA)
    assert result.risks.overall_risk == RiskLevel.LOW
    flood_flags = [f for f in result.risks.flags if "flood" in f.code]
    assert flood_flags == []


def test_flag_flood_zone_ae_high_risk():
    result = flag_risks(_analyzed(flood_zone="AE"), CRITERIA)
    assert result.risks.overall_risk == RiskLevel.HIGH
    flood_flags = [f for f in result.risks.flags if f.code == "flood_zone_high"]
    assert len(flood_flags) == 1


@pytest.mark.parametrize("zone", ["A", "AE", "AH", "AO", "AR", "A99", "V", "VE"])
def test_flag_all_high_risk_zones(zone):
    result = flag_risks(_analyzed(flood_zone=zone), CRITERIA)
    assert result.risks.overall_risk == RiskLevel.HIGH


def test_flag_flood_zone_unknown_no_flag():
    """Unknown flood zone (FEMA unavailable) should not produce a high-risk flag."""
    result = flag_risks(_analyzed(flood_zone=None), CRITERIA)
    # flood_zone=None → FEMA lookup attempted (will gracefully return None in unit test)
    # Result should not produce HIGH risk from flood zone alone
    flood_flags = [f for f in result.risks.flags if "flood" in f.code]
    assert flood_flags == []


# ── DOM outlier ────────────────────────────────────────────────────────────────

def test_flag_dom_zero_not_flagged():
    """DOM=0 (just listed) must never be flagged as a DOM outlier."""
    result = flag_risks(_analyzed(dom=0), CRITERIA, dom_baseline=15.0)
    dom_flags = [f for f in result.risks.flags if f.code == "high_dom"]
    assert dom_flags == []


def test_flag_dom_normal_not_flagged():
    result = flag_risks(_analyzed(dom=15), CRITERIA, dom_baseline=15.0)
    dom_flags = [f for f in result.risks.flags if f.code == "high_dom"]
    assert dom_flags == []


def test_flag_dom_outlier_flagged():
    """DOM > baseline * multiplier should produce a MEDIUM risk flag."""
    # baseline=15, multiplier=2.0 → threshold=30. DOM=31 → flagged.
    result = flag_risks(_analyzed(dom=31), CRITERIA, dom_baseline=15.0)
    dom_flags = [f for f in result.risks.flags if f.code == "high_dom"]
    assert len(dom_flags) == 1
    assert dom_flags[0].level == RiskLevel.MEDIUM


def test_flag_dom_at_exact_threshold_not_flagged():
    """DOM exactly at threshold is not an outlier (> not >=)."""
    result = flag_risks(_analyzed(dom=30), CRITERIA, dom_baseline=15.0)
    dom_flags = [f for f in result.risks.flags if f.code == "high_dom"]
    assert dom_flags == []


def test_flag_no_dom_baseline_skips_dom_check():
    """If dom_baseline is None, DOM outlier check is skipped entirely."""
    result = flag_risks(_analyzed(dom=999), CRITERIA, dom_baseline=None)
    dom_flags = [f for f in result.risks.flags if f.code == "high_dom"]
    assert dom_flags == []


# ── flag_all ───────────────────────────────────────────────────────────────────

def test_flag_all_computes_dom_baseline():
    """flag_all() should compute baseline from the provided listings, not a fixed value."""
    listings = [
        _analyzed(dom=10),
        _analyzed(dom=10),
        _analyzed(dom=10),
        _analyzed(dom=100),  # outlier: 100 > (10 * 2.0) = 20
    ]
    flagged = flag_all(listings, CRITERIA)
    outlier = flagged[3]
    dom_flags = [f for f in outlier.risks.flags if f.code == "high_dom"]
    assert len(dom_flags) == 1


# ── Overall risk aggregation ───────────────────────────────────────────────────

def test_overall_risk_high_when_any_flag_is_high():
    result = flag_risks(_analyzed(flood_zone="AE", dom=100), CRITERIA, dom_baseline=10.0)
    assert result.risks.overall_risk == RiskLevel.HIGH


def test_overall_risk_medium_when_only_medium_flags():
    result = flag_risks(_analyzed(flood_zone="X", dom=100), CRITERIA, dom_baseline=10.0)
    assert result.risks.overall_risk == RiskLevel.MEDIUM


def test_overall_risk_low_when_no_flags():
    result = flag_risks(_analyzed(flood_zone="X", dom=10), CRITERIA, dom_baseline=15.0)
    assert result.risks.overall_risk == RiskLevel.LOW
