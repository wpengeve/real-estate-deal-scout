"""
Tests for market context flowing through the pipeline into the ranker.

Covers the two things that make this more than a display feature: the ranker
sees the same numbers the report shows, and unreliable percentages are withheld
from the model just as they are from the reader.
"""
import pytest

import pipeline as pipeline_mod
import tools.report as report_mod
from tools.models import (
    DealNarrative,
    FinancialResult,
    FlaggedListing,
    MarketSnapshot,
    RawListing,
    RiskLevel,
    RiskResult,
)


def _flagged(address="4521 Rainier Ave S, Seattle, WA 98118", price=900_000.0, sqft=1_500):
    return FlaggedListing(
        listing=RawListing(zpid="1", address=address, price=price, sqft=sqft),
        financials=FinancialResult(success=True, cap_rate=0.05),
        risks=RiskResult(overall_risk=RiskLevel.LOW),
    )


def _snapshot(**overrides) -> MarketSnapshot:
    base = dict(
        city="Seattle", state="WA", period_end="2026-05-31",
        property_type="All Residential", homes_sold=799,
        median_ppsf=600.0, months_of_supply=2.6, median_dom=11.0,
        sale_to_list=1.008, pct_above_list=0.309, pct_price_drops=0.21,
    )
    base.update(overrides)
    return MarketSnapshot(**base)


# ── _attach_market_context ────────────────────────────────────────────────────

def test_attaches_context_to_each_listing(monkeypatch):
    monkeypatch.setattr(
        pipeline_mod.market_trends, "snapshot_for_address", lambda *a, **k: _snapshot()
    )
    flagged = [_flagged(), _flagged(address="1 B St, Seattle, WA 98115")]
    pipeline_mod._attach_market_context(flagged)
    assert all(f.market_context is not None for f in flagged)


def test_missing_slice_leaves_context_none(monkeypatch):
    monkeypatch.setattr(
        pipeline_mod.market_trends, "snapshot_for_address", lambda *a, **k: None
    )
    flagged = [_flagged()]
    pipeline_mod._attach_market_context(flagged)
    assert flagged[0].market_context is None


def test_lookup_failure_does_not_break_the_run(monkeypatch):
    """Optional context must never take down a pipeline run."""
    def boom(*a, **k):
        raise RuntimeError("corrupt slice")
    monkeypatch.setattr(pipeline_mod.market_trends, "snapshot_for_address", boom)

    flagged = [_flagged()]
    pipeline_mod._attach_market_context(flagged)   # must not raise
    assert flagged[0].market_context is None


# ── _market_context_for_prompt ────────────────────────────────────────────────

def test_prompt_context_none_without_data():
    assert pipeline_mod._market_context_for_prompt(_flagged()) is None


def test_prompt_context_includes_rates_when_sample_is_large():
    f = _flagged()
    f.market_context = _snapshot()
    ctx = pipeline_mod._market_context_for_prompt(f)

    assert ctx["market_temperature"] == "Seller's market"
    assert ctx["avg_sale_to_final_list_price"] == 1.008
    assert ctx["pct_sold_above_final_list"] == 0.309
    assert ctx["pct_listings_with_price_cut"] == 0.21
    assert "rates_withheld" not in ctx


def test_prompt_context_withholds_rates_on_small_sample():
    """The model must not see '100% above list' off one sale either."""
    f = _flagged()
    f.market_context = _snapshot(
        city="Alger", homes_sold=1, pct_above_list=1.0, sale_to_list=1.0003
    )
    ctx = pipeline_mod._market_context_for_prompt(f)

    assert "avg_sale_to_final_list_price" not in ctx
    assert "pct_sold_above_final_list" not in ctx
    assert "pct_listings_with_price_cut" not in ctx
    assert ctx["rates_withheld"]
    assert ctx["homes_sold_that_month"] == 1
    assert ctx["months_of_supply"] == 2.6   # supply metrics stay valid


def test_prompt_context_compares_price_per_sqft():
    f = _flagged(price=900_000.0, sqft=1_500)      # $600/sqft
    f.market_context = _snapshot(median_ppsf=500.0)
    ctx = pipeline_mod._market_context_for_prompt(f)

    assert ctx["this_home_ppsf"] == 600
    assert ctx["city_median_ppsf"] == 500
    assert ctx["ppsf_vs_city_median_pct"] == 20.0


def test_prompt_context_skips_comparison_without_sqft():
    f = _flagged(sqft=None)
    f.market_context = _snapshot()
    ctx = pipeline_mod._market_context_for_prompt(f)

    assert "this_home_ppsf" not in ctx
    assert ctx["city_median_ppsf"] == 600


def test_prompt_guidance_carries_the_caveats():
    """
    The model sees only the JSON, so the final-list-price and staleness caveats
    have to be in the instructions.
    """
    guidance = pipeline_mod._MARKET_GUIDANCE
    assert "FINAL list price" in guidance
    assert "pct_listings_with_price_cut" in guidance
    assert "rates_withheld" in guidance
    assert "never present it as current conditions" in guidance
    assert "never add a fourth line" in guidance


# ── report prefers pipeline-attached context ──────────────────────────────────

def test_report_uses_attached_context_without_lookup(monkeypatch):
    """
    When the pipeline attached context, the report must use it rather than
    re-reading the slice — otherwise strip and narrative could disagree.
    """
    def fail(*a, **k):
        raise AssertionError("should not fall back to a lookup")
    monkeypatch.setattr(report_mod.market_trends, "snapshot_for_address", fail)

    deal = DealNarrative(
        rank=1, address="4521 Rainier Ave S, Seattle, WA 98118",
        price=900_000.0, sqft=1_500, risk_level="LOW", narrative="x",
        market_context=_snapshot(city="Tacoma"),
    )
    assert "Market Context · Tacoma, WA" in report_mod._render_market_snapshot(deal)


def test_report_falls_back_for_older_shortlists(monkeypatch):
    """Shortlists saved before this feature carry no context; look it up."""
    monkeypatch.setattr(
        report_mod.market_trends, "snapshot_for_address", lambda *a, **k: _snapshot()
    )
    deal = DealNarrative(
        rank=1, address="4521 Rainier Ave S, Seattle, WA 98118",
        price=900_000.0, sqft=1_500, risk_level="LOW", narrative="x",
    )
    assert "Market Context · Seattle, WA" in report_mod._render_market_snapshot(deal)


def test_market_context_survives_json_round_trip():
    """The shortlist is persisted to JSON and reloaded by the web app."""
    deal = DealNarrative(
        rank=1, address="1 A St, Seattle, WA 98115", price=900_000.0,
        risk_level="LOW", narrative="x", market_context=_snapshot(),
    )
    restored = DealNarrative.model_validate_json(deal.model_dump_json())

    assert restored.market_context.city == "Seattle"
    assert restored.market_context.has_enough_sales is True
    assert restored.market_context.market_temperature == "Seller's market"


def test_flagged_listing_round_trips_context():
    """--from-analyzed reloads FlaggedListing from disk."""
    f = _flagged()
    f.market_context = _snapshot()
    restored = FlaggedListing.model_validate_json(f.model_dump_json())
    assert restored.market_context.period_end == "2026-05-31"
