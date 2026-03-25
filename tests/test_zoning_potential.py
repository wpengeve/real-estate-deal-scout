"""
Unit tests for tools/zoning_potential.py — pure rule-based engine, no I/O.
"""
import pytest
from tools.zoning_potential import assess


# ── Seattle NR zones ───────────────────────────────────────────────────────────

def test_nr1_small_lot_no_dadu():
    zp = assess("NR1", 2800, "123 Main St, Seattle, WA 98101")
    assert zp.zoned_units == 2
    assert zp.adu_eligible is True
    assert zp.dadu_eligible is False   # lot < 3,200 sqft
    assert zp.development_score == 2


def test_nr1_large_lot_dadu_eligible():
    zp = assess("NR1", 5000, "123 Main St, Seattle, WA 98101")
    assert zp.dadu_eligible is True
    assert zp.development_score == 3


def test_nr2_returns_4_units():
    zp = assess("NR2", 8000, "456 Oak Ave, Seattle, WA 98115")
    assert zp.zoned_units == 4
    assert zp.dadu_eligible is True
    assert zp.development_score >= 4


def test_nr2_subdivision_eligible_on_large_lot():
    zp = assess("NR2", 10000, "456 Oak Ave, Seattle, WA 98115")
    assert zp.subdivision_eligible is True
    assert zp.development_score == 5


def test_nr3_returns_6_units():
    zp = assess("NR3", 10000, "789 Pine St, Seattle, WA 98103")
    assert zp.zoned_units == 6
    assert zp.development_score >= 4


def test_nr2_mha_suffix_stripped():
    """Zoning codes like 'NR2 (M)' should be treated as NR2."""
    zp = assess("NR2 (M)", 8000, "456 Oak Ave, Seattle, WA 98115")
    assert zp.zoned_units == 4


def test_nr2_m1_suffix_stripped():
    zp = assess("NR2 (M1)", 8000, "456 Oak Ave, Seattle, WA 98115")
    assert zp.zoned_units == 4


# ── Seattle LR zones ───────────────────────────────────────────────────────────

def test_lr1_scores_5():
    zp = assess("LR1", 6000, "100 Broadway, Seattle, WA 98122")
    assert zp.development_score == 5
    assert zp.zoned_units is not None and zp.zoned_units >= 1


def test_lr2_high_unit_count():
    zp = assess("LR2", 10000, "200 Madison St, Seattle, WA 98104")
    assert zp.development_score == 5
    # 10,000 sqft / 43,560 * 29 units/acre ≈ 6 units
    assert zp.zoned_units is not None and zp.zoned_units >= 4


# ── King County unincorporated ─────────────────────────────────────────────────

def test_r4_calculates_units_from_lot():
    # 21,780 sqft = 0.5 acre × 4 units/acre = 2 units
    zp = assess("R-4", 21780, "999 Rural Rd, Renton, WA 98059")
    assert zp.zoned_units == 2


def test_r4_dadu_eligible_on_large_lot():
    zp = assess("R-4", 10000, "999 Rural Rd, Renton, WA 98059")
    assert zp.adu_eligible is True
    assert zp.dadu_eligible is True


def test_r8_higher_density_than_r4():
    zp_r4 = assess("R-4", 43560, "A, WA 98059")   # 1 acre
    zp_r8 = assess("R-8", 43560, "B, WA 98059")
    assert (zp_r8.zoned_units or 0) > (zp_r4.zoned_units or 0)


# ── WA HB 1110 ────────────────────────────────────────────────────────────────

def test_hb1110_applies_to_wa_address():
    zp = assess(None, None, "123 Main St, Tacoma, WA 98401")
    assert zp.hb1110_duplex is True


def test_hb1110_assumed_when_no_address():
    zp = assess(None, None, None)
    assert zp.hb1110_duplex is True


def test_hb1110_does_not_apply_outside_wa():
    zp = assess(None, None, "123 Main St, Portland, OR 97201")
    assert zp.hb1110_duplex is False


# ── Unknown zoning ─────────────────────────────────────────────────────────────

def test_unknown_zoning_large_lot_suggests_adu():
    zp = assess("UNKNOWN-ZONE", 5000, "123 Main St, Bothell, WA 98012")
    assert zp.adu_eligible is True
    assert zp.dadu_eligible is True


def test_unknown_zoning_small_lot_no_adu():
    zp = assess("UNKNOWN-ZONE", 2000, "123 Main St, Bothell, WA 98012")
    assert zp.adu_eligible is False
    assert zp.dadu_eligible is False


def test_no_zoning_no_lot_returns_model():
    """Should not raise — just return a low-score result."""
    zp = assess(None, None, None)
    assert zp.development_score is not None
    assert isinstance(zp.opportunities, list)


# ── Score boundaries ───────────────────────────────────────────────────────────

def test_score_range_is_1_to_5():
    cases = [
        ("NR1", 2000),
        ("NR1", 5000),
        ("NR2", 8000),
        ("NR3", 12000),
        ("LR1", 8000),
        (None, None),
        ("R-4", 10000),
    ]
    for zoning, lot in cases:
        zp = assess(zoning, lot, "Seattle, WA 98101")
        assert 1 <= (zp.development_score or 1) <= 5, f"Out of range for {zoning}/{lot}"


# ── Summary and opportunities ──────────────────────────────────────────────────

def test_nr2_summary_mentions_units():
    zp = assess("NR2", 8000, "456 Oak Ave, Seattle, WA 98115")
    assert zp.summary is not None
    assert "4" in zp.summary or "units" in zp.summary.lower()


def test_opportunities_not_empty_for_nr2():
    zp = assess("NR2", 8000, "456 Oak Ave, Seattle, WA 98115")
    assert len(zp.opportunities) >= 2
