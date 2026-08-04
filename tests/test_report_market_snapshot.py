"""
Tests for the market-context strip in tools/report.py.

The strip is additive: it must render nothing at all rather than break a report
when there's no local slice, and it must never show percentages computed from a
handful of sales.
"""
import pytest

import tools.report as report_mod
from tools.market_trends import MarketSnapshot
from tools.models import DealNarrative


def _deal(**overrides) -> DealNarrative:
    base = dict(
        rank=1,
        address="4521 Rainier Ave S, Seattle, WA 98118",
        price=900_000.0,
        sqft=1_500,
        risk_level="LOW",
        narrative="A solid rental.",
    )
    base.update(overrides)
    return DealNarrative(**base)


def _snapshot(**overrides) -> MarketSnapshot:
    base = dict(
        city="Seattle", state="WA", period_end="2026-05-31",
        property_type="All Residential", homes_sold=799,
        median_sale_price=915_000.0, median_list_price=899_000.0,
        median_ppsf=600.0, inventory=2111.0, new_listings=1400.0,
        pending_sales=830.0, months_of_supply=2.6, median_dom=11.0,
        sale_to_list=1.008, pct_above_list=0.309, pct_price_drops=0.21,
        pct_off_market_two_weeks=0.55, median_sale_price_yoy=0.03,
        median_dom_yoy=-0.1, sale_to_list_yoy=0.002, inventory_yoy=0.12,
    )
    base.update(overrides)
    return MarketSnapshot(**base)


@pytest.fixture
def patched(monkeypatch):
    """Force a given snapshot (or None) for whatever address is rendered."""
    def _apply(snapshot):
        monkeypatch.setattr(
            report_mod.market_trends, "snapshot_for_address",
            lambda *a, **k: snapshot,
        )
    return _apply


def test_renders_nothing_without_data(patched):
    """No refresh run yet must mean an unchanged report, not an error."""
    patched(None)
    assert report_mod._render_market_snapshot(_deal()) == ""


def test_renders_headline_metrics(patched):
    patched(_snapshot())
    html = report_mod._render_market_snapshot(_deal())

    assert "Market Context · Seattle, WA" in html
    assert "data as of 2026-05-31" in html
    assert "Seller's market" in html      # 2.6 months of supply
    assert "2.6 mo supply" in html
    assert "1.008" in html
    assert "31%" in html                  # sold above ask
    assert "21%" in html                  # price drops
    assert "11" in html                   # median days to contract
    assert "Redfin" in html               # attribution


def test_price_drops_shown_alongside_sale_to_list(patched):
    """
    Sale-to-list measures against the *reduced* price, so the drop rate is the
    counterweight. If one renders, the other must too.
    """
    patched(_snapshot())
    html = report_mod._render_market_snapshot(_deal())
    assert "Sale-to-list" in html and "Price drops" in html


def test_small_sample_suppresses_percentages(patched):
    """Alger's real row: 1 sale reporting 100% above list."""
    patched(_snapshot(
        city="Alger", homes_sold=1, pct_above_list=1.0,
        sale_to_list=1.0003, pct_price_drops=0.0, months_of_supply=8.0,
        median_dom=3.0,
    ))
    html = report_mod._render_market_snapshot(_deal(address="1 Rural Rd, Alger, WA 98233"))

    assert "Sale-to-list" not in html
    assert "Sold above ask" not in html
    assert "100%" not in html
    assert "Only 1 home sold" in html
    assert "Buyer's market" in html       # supply-based metrics stay valid
    assert "Median days to contract" in html


def test_small_sample_note_pluralises(patched):
    patched(_snapshot(homes_sold=4))
    html = report_mod._render_market_snapshot(_deal())
    assert "Only 4 homes sold" in html


def test_deal_priced_above_market(patched):
    """$900k / 1500 sqft = $600/sqft vs a $500 median -> 20% above."""
    patched(_snapshot(median_ppsf=500.0))
    html = report_mod._render_market_snapshot(_deal())
    assert "$600/sqft" in html
    assert "20% above the Seattle median" in html


def test_deal_priced_below_market(patched):
    patched(_snapshot(median_ppsf=750.0))
    html = report_mod._render_market_snapshot(_deal())
    assert "20% below the Seattle median" in html


def test_deal_priced_in_line_with_market(patched):
    patched(_snapshot(median_ppsf=600.0))
    html = report_mod._render_market_snapshot(_deal())
    assert "in line with the Seattle median" in html


def test_comparison_omitted_without_sqft(patched):
    patched(_snapshot())
    html = report_mod._render_market_snapshot(_deal(sqft=None))
    assert "/sqft" not in html
    assert "Market Context" in html       # the rest still renders


def test_missing_metrics_are_skipped_not_zeroed(patched):
    """NA upstream values must drop their tile rather than render as 0."""
    patched(_snapshot(months_of_supply=None, median_dom=None))
    html = report_mod._render_market_snapshot(_deal())
    assert "Median days to contract" not in html
    assert "mo supply" not in html
    assert "Sale-to-list" in html


def test_renders_nothing_when_all_metrics_missing(patched):
    patched(_snapshot(
        homes_sold=None, months_of_supply=None, median_dom=None,
        sale_to_list=None, pct_above_list=None, pct_price_drops=None,
    ))
    assert report_mod._render_market_snapshot(_deal()) == ""


def test_strip_is_included_in_the_card(patched):
    """Guard the wiring, not just the helper."""
    patched(_snapshot())
    card = report_mod._render_deal(_deal(), idx=0)
    assert "Market Context · Seattle, WA" in card
