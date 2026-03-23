"""
Tests for tools/analyze.py — financial calculations.

All tests use known inputs to verify formula correctness.
Negative cash flow is valid and is NOT treated as an error.
"""
import pytest

from tools.models import EnrichResult, FinancialAssumptions, RawListing
from tools.analyze import analyze_financials, _amortization_payment

ASSUMPTIONS = FinancialAssumptions(
    down_payment_pct=0.25,
    loan_rate_annual=0.07,
    loan_term_years=30,
    vacancy_rate=0.08,
    management_fee_pct=0.10,
    maintenance_pct_of_value=0.01,
    insurance_annual=1200.0,
    closing_cost_pct=0.03,
    property_tax_rate_pct=0.012,
)


def _enrich(price=400_000, rent=2500.0, property_tax=None, walk_score=55) -> EnrichResult:
    listing = RawListing(
        zpid="test",
        address="123 Main St",
        price=price,
        beds=3,
        days_on_market=10,
        estimated_monthly_rent=None,  # rent comes via EnrichResult
        property_tax_annual=property_tax,
    )
    return EnrichResult(
        listing=listing,
        walk_score=walk_score,
        estimated_monthly_rent=rent,
    )


# ── Amortization ───────────────────────────────────────────────────────────────

def test_amortization_known_value():
    """
    $300k loan, 7% annual, 30 years.
    Standard mortgage calculator: ~$1,995.91/mo.
    """
    payment = _amortization_payment(300_000, 0.07, 30)
    assert abs(payment - 1995.91) < 1.0, f"Got {payment:.2f}"


def test_amortization_zero_rate():
    """Zero-rate edge case: payment = principal / n."""
    payment = _amortization_payment(120_000, 0.0, 10)
    assert abs(payment - 1_000.0) < 0.01


# ── Cap rate ───────────────────────────────────────────────────────────────────

def test_analyze_cap_rate():
    """
    price=400k, rent=$2500/mo, tax=$4800/yr (listing data)
    Verify cap rate is within a reasonable band (~5-7%).
    """
    result = analyze_financials(_enrich(price=400_000, rent=2500, property_tax=4800), ASSUMPTIONS)
    assert result.financials.success is True
    assert result.financials.cap_rate is not None
    # Sanity-check the formula direction (typical range for Seattle market inputs)
    assert 0.02 < result.financials.cap_rate < 0.09


def test_analyze_cap_rate_formula():
    """
    Hand-calculated cap rate for known inputs:
    price=285000, rent=2150, tax=5985 (from fixture 10001)

    gross_rent   = 2150 * 12 = 25800
    eff_rent     = 25800 * (1 - 0.08) = 23736
    management   = 23736 * 0.10 = 2373.6
    maintenance  = 285000 * 0.01 = 2850
    NOI = 23736 - 2373.6 - 2850 - 1200 - 5985 = 11327.4
    cap_rate = 11327.4 / 285000 ≈ 0.0397
    """
    result = analyze_financials(
        _enrich(price=285_000, rent=2150, property_tax=5985), ASSUMPTIONS
    )
    assert result.financials.success is True
    expected_cap_rate = 11327.4 / 285_000
    assert abs(result.financials.cap_rate - expected_cap_rate) < 0.001


# ── Monthly cash flow ──────────────────────────────────────────────────────────

def test_analyze_monthly_cashflow_positive():
    """High-yield property should produce positive cash flow."""
    # Low price, good rent — should be clearly positive
    result = analyze_financials(_enrich(price=250_000, rent=2500), ASSUMPTIONS)
    assert result.financials.success is True
    assert result.financials.monthly_cashflow is not None
    assert result.financials.monthly_cashflow > 0


def test_analyze_monthly_cashflow_negative_is_valid():
    """Negative cash flow is a valid result, not an error."""
    # High price, low rent — likely negative
    result = analyze_financials(_enrich(price=600_000, rent=2000), ASSUMPTIONS)
    assert result.financials.success is True  # still success
    assert result.financials.monthly_cashflow is not None
    assert result.financials.monthly_cashflow < 0  # expect negative


# ── Cash-on-cash return ────────────────────────────────────────────────────────

def test_analyze_coc_return():
    result = analyze_financials(_enrich(price=400_000, rent=2500), ASSUMPTIONS)
    assert result.financials.success is True
    assert result.financials.coc_return is not None
    # Positive cash flow should yield positive CoC
    # (sign depends on cash flow; just verify it's computed)
    assert isinstance(result.financials.coc_return, float)


# ── Failure cases ──────────────────────────────────────────────────────────────

def test_analyze_fails_when_no_rent():
    listing = RawListing(zpid="x", address="x", price=400_000, beds=3, days_on_market=10)
    enrich_result = EnrichResult(listing=listing, walk_score=50, estimated_monthly_rent=None)
    result = analyze_financials(enrich_result, ASSUMPTIONS)
    assert result.financials.success is False
    assert result.financials.failure_reason == "no_rent_data"


def test_analyze_fails_when_price_zero():
    listing = RawListing(zpid="x", address="x", price=0, beds=3, days_on_market=10)
    enrich_result = EnrichResult(listing=listing, walk_score=50, estimated_monthly_rent=2000)
    result = analyze_financials(enrich_result, ASSUMPTIONS)
    assert result.financials.success is False
    assert result.financials.failure_reason == "invalid_price"


def test_analyze_fails_when_price_none():
    listing = RawListing(zpid="x", address="x", price=None, beds=3, days_on_market=10)
    enrich_result = EnrichResult(listing=listing, walk_score=50, estimated_monthly_rent=2000)
    result = analyze_financials(enrich_result, ASSUMPTIONS)
    assert result.financials.success is False


# ── Property tax fallback ──────────────────────────────────────────────────────

def test_analyze_uses_listing_tax_when_available():
    """If listing has property_tax_annual, it should be used (not the rate-based fallback)."""
    r_with_tax = analyze_financials(_enrich(price=400_000, property_tax=8000), ASSUMPTIONS)
    r_without_tax = analyze_financials(_enrich(price=400_000, property_tax=None), ASSUMPTIONS)
    # 1.2% of 400k = 4800 (fallback), vs 8000 (explicit)
    # Higher tax → lower NOI → lower cap rate
    assert r_with_tax.financials.cap_rate < r_without_tax.financials.cap_rate
