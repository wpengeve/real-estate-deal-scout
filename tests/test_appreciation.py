"""
Tests for tools/appreciation.py — appreciation signal computation.

All pure functions, no I/O.
"""
import pytest
from tools.appreciation import compute_appreciation_signals
from tools.models import AppreciationSignals, RawListing


def _listing(**kwargs) -> RawListing:
    """Build a minimal RawListing with sensible defaults."""
    defaults = {
        "zpid": "1",
        "address": "123 Test St, Seattle, WA 98118",
        "price": 500_000.0,
    }
    defaults.update(kwargs)
    return RawListing(**defaults)


# ── No price / degenerate inputs ───────────────────────────────────────────────

def test_returns_baseline_when_no_price():
    result = compute_appreciation_signals(_listing(price=None), estimated_monthly_rent=2000)
    assert result.appreciation_score == 1
    assert result.signals == ["Price unavailable"]


def test_returns_baseline_when_price_zero():
    result = compute_appreciation_signals(_listing(price=0), estimated_monthly_rent=2000)
    assert result.appreciation_score == 1


def test_returns_baseline_when_price_negative():
    result = compute_appreciation_signals(_listing(price=-1), estimated_monthly_rent=2000)
    assert result.appreciation_score == 1


def test_no_rent_skips_grm():
    result = compute_appreciation_signals(_listing(price=500_000), estimated_monthly_rent=None)
    assert result.price_to_rent_ratio is None
    assert not any("GRM" in s for s in result.signals)


def test_zero_rent_skips_grm():
    result = compute_appreciation_signals(_listing(price=500_000), estimated_monthly_rent=0)
    assert result.price_to_rent_ratio is None


# ── GRM (price-to-rent ratio) ─────────────────────────────────────────────────

def test_excellent_grm_adds_two_points():
    # GRM = 500_000 / (3500*12) = ~11.9
    result = compute_appreciation_signals(_listing(price=500_000), estimated_monthly_rent=3_500)
    assert result.price_to_rent_ratio is not None
    assert result.price_to_rent_ratio <= 15
    assert result.appreciation_score >= 3  # baseline 1 + 2 for excellent GRM
    assert any("Excellent GRM" in s for s in result.signals)


def test_good_grm_adds_one_point():
    # GRM = 700_000 / (3000*12) = ~19.4
    result = compute_appreciation_signals(_listing(price=700_000), estimated_monthly_rent=3_000)
    assert result.price_to_rent_ratio is not None
    assert 15 < result.price_to_rent_ratio <= 22
    assert any("Good GRM" in s for s in result.signals)


def test_near_median_grm_no_bonus():
    # GRM = 700_000 / (2200*12) = ~26.5, within Seattle median (28)
    result = compute_appreciation_signals(_listing(price=700_000), estimated_monthly_rent=2_200)
    assert result.price_to_rent_ratio is not None
    assert 22 < result.price_to_rent_ratio <= 28
    assert any("near Seattle median" in s for s in result.signals)


def test_high_grm_no_bonus():
    # GRM = 900_000 / (2000*12) = 37.5 > 28
    result = compute_appreciation_signals(_listing(price=900_000), estimated_monthly_rent=2_000)
    assert result.price_to_rent_ratio is not None
    assert result.price_to_rent_ratio > 28
    assert any("below-average yield" in s for s in result.signals)


def test_grm_stored_rounded():
    result = compute_appreciation_signals(_listing(price=500_000), estimated_monthly_rent=3_500)
    # 500000 / (3500*12) = 11.904... → rounded to 2dp
    assert result.price_to_rent_ratio == round(500_000 / (3_500 * 12), 2)


# ── Assessment ratio ───────────────────────────────────────────────────────────

def test_heavily_underassessed_adds_one_point():
    # ratio = 300_000 / 500_000 = 0.60 < 0.65
    result = compute_appreciation_signals(
        _listing(price=500_000, tax_assessed_value=300_000),
        estimated_monthly_rent=None,
    )
    assert result.assessment_ratio == pytest.approx(0.6, rel=1e-3)
    assert any("underassessed" in s for s in result.signals)
    assert result.appreciation_score >= 2


def test_moderately_underassessed_no_bonus():
    # ratio = 370_000 / 500_000 = 0.74 → between 0.65 and 0.80
    result = compute_appreciation_signals(
        _listing(price=500_000, tax_assessed_value=370_000),
        estimated_monthly_rent=None,
    )
    assert any("moderately underassessed" in s for s in result.signals)
    # No score bonus for moderate underassessment
    assert result.appreciation_score == 1


def test_overassessed_note():
    # ratio = 600_000 / 500_000 = 1.2 > 1.10
    result = compute_appreciation_signals(
        _listing(price=500_000, tax_assessed_value=600_000),
        estimated_monthly_rent=None,
    )
    assert any("above market" in s for s in result.signals)


def test_no_assessed_value_skips_ratio():
    result = compute_appreciation_signals(
        _listing(price=500_000, tax_assessed_value=None),
        estimated_monthly_rent=None,
    )
    assert result.assessment_ratio is None
    assert not any("assessed" in s.lower() for s in result.signals)


# ── Land value percentage ──────────────────────────────────────────────────────

def test_land_heavy_adds_one_point():
    # land 490_000 / total 600_000 = 0.817 ≥ 0.70
    result = compute_appreciation_signals(
        _listing(price=800_000, tax_assessed_value=600_000, tax_assessed_land=490_000),
        estimated_monthly_rent=None,
    )
    assert result.land_value_pct is not None
    assert result.land_value_pct >= 0.70
    assert any("Land-heavy" in s for s in result.signals)
    assert result.appreciation_score >= 2


def test_balanced_land_split_noted():
    # land 350_000 / total 600_000 = 0.583 → between 0.55 and 0.70
    result = compute_appreciation_signals(
        _listing(price=800_000, tax_assessed_value=600_000, tax_assessed_land=350_000),
        estimated_monthly_rent=None,
    )
    assert any("Balanced" in s for s in result.signals)
    assert result.appreciation_score == 1  # no bonus


def test_no_land_value_skips_pct():
    result = compute_appreciation_signals(
        _listing(price=500_000, tax_assessed_value=400_000, tax_assessed_land=None),
        estimated_monthly_rent=None,
    )
    assert result.land_value_pct is None


# ── Renovation candidate ───────────────────────────────────────────────────────

def test_pre_1975_and_underassessed_adds_one_point():
    result = compute_appreciation_signals(
        _listing(price=500_000, year_built=1960, tax_assessed_value=300_000),
        estimated_monthly_rent=None,
    )
    assert result.renovation_candidate is True
    assert any("renovation upside" in s for s in result.signals)
    # assessment ratio < 0.65 → +1, renovation+underassessed → +1, baseline 1 = 3
    assert result.appreciation_score >= 3


def test_pre_1975_assessed_ok_still_flagged():
    result = compute_appreciation_signals(
        _listing(price=500_000, year_built=1968, tax_assessed_value=420_000),
        estimated_monthly_rent=None,
    )
    assert result.renovation_candidate is True
    assert any("renovation may be needed" in s for s in result.signals)


def test_post_1975_not_renovation_candidate():
    result = compute_appreciation_signals(
        _listing(price=500_000, year_built=1990),
        estimated_monthly_rent=None,
    )
    assert result.renovation_candidate is False
    assert not any("renovation" in s.lower() for s in result.signals)


def test_no_year_built_skips_renovation():
    result = compute_appreciation_signals(
        _listing(price=500_000, year_built=None),
        estimated_monthly_rent=None,
    )
    assert result.renovation_candidate is False


# ── Score clamping ─────────────────────────────────────────────────────────────

def test_score_clamped_to_5():
    # Everything excellent: GRM ≤ 15, heavily underassessed, land-heavy, pre-1975 + underassessed
    result = compute_appreciation_signals(
        _listing(
            price=500_000,
            year_built=1960,
            tax_assessed_value=280_000,  # ratio 0.56 < 0.65 → +1; also triggers reno+underassessed → +1
            tax_assessed_land=220_000,   # land pct = 220/280 = 0.786 ≥ 0.70 → +1
        ),
        estimated_monthly_rent=3_500,   # GRM ≈ 11.9 ≤ 15 → +2
    )
    assert result.appreciation_score == 5


def test_score_never_below_1():
    result = compute_appreciation_signals(
        _listing(price=1_000_000),
        estimated_monthly_rent=None,
    )
    assert result.appreciation_score == 1


# ── Combined / integration ─────────────────────────────────────────────────────

def test_all_none_fields_produce_empty_signals_list():
    """Listing with only price — no signals from missing fields."""
    result = compute_appreciation_signals(
        _listing(price=600_000),
        estimated_monthly_rent=None,
    )
    assert isinstance(result, AppreciationSignals)
    assert result.appreciation_score == 1
    assert result.price_to_rent_ratio is None
    assert result.assessment_ratio is None
    assert result.land_value_pct is None
    assert result.renovation_candidate is False
