"""
Integration tests for the full pipeline.

External API calls (WalkScore, FEMA, Anthropic) are mocked.
The fixture file is loaded from the real fixtures/listings.json.
"""
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tools.models import (
    DealNarrative,
    FinancialAssumptions,
    FinancialResult,
    InvestmentConfig,
    OutputConfig,
    RawListing,
    ScreeningCriteria,
    Shortlist,
)

CONFIG = InvestmentConfig(
    criteria=ScreeningCriteria(max_price=500_000, min_beds=3, max_dom=30),
    financial_assumptions=FinancialAssumptions(),
    output=OutputConfig(market="Austin, TX", max_shortlist=5),
)

_FIXTURE_PATH = Path(__file__).parent.parent / "fixtures" / "listings.json"

_MOCK_SHORTLIST = Shortlist(
    market="Austin, TX",
    deals=[
        DealNarrative(
            rank=1,
            address="9012 Airport Blvd, Austin, TX 78729",
            price=285_000,
            beds=3,
            days_on_market=8,
            cap_rate=0.040,
            coc_return=0.085,
            monthly_cashflow=245.0,
            walk_score=42,
            flood_zone="X",
            risk_level="LOW",
            narrative="Strong rent-to-price ratio anchored by $2,150/mo rent comps.",
        )
    ],
    run_summary="1 of 13 screened listings ranked.",
)


def _mock_claude_response(shortlist: Shortlist):
    """Build a mock Anthropic API response that returns the given shortlist."""
    tool_use_block = MagicMock()
    tool_use_block.type = "tool_use"
    tool_use_block.input = shortlist.model_dump()

    response = MagicMock()
    response.content = [tool_use_block]
    return response


# ── Full pipeline: fixtures → shortlist ────────────────────────────────────────

@pytest.mark.asyncio
async def test_pipeline_fixture_to_shortlist(tmp_path):
    """
    Friday-night test: loads fixtures/listings.json, runs through the full
    pipeline with mocked Claude, asserts shortlist has ≥1 property with
    non-null financials and a non-empty narrative.
    """
    from pipeline import run

    with patch("pipeline.anthropic.AsyncAnthropic") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(
            return_value=_mock_claude_response(_MOCK_SHORTLIST)
        )
        mock_client_cls.return_value = mock_client

        shortlist = await run("Austin, TX", CONFIG)

    assert len(shortlist.deals) >= 1
    deal = shortlist.deals[0]
    assert deal.cap_rate is not None
    assert deal.monthly_cashflow is not None
    assert deal.narrative and len(deal.narrative) > 10


# ── 0 listings pass screening ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_pipeline_zero_screened_exits_gracefully(tmp_path):
    """
    When no listings pass screening, the pipeline returns an empty shortlist
    without calling Claude.
    """
    from pipeline import run

    # Use very tight criteria so nothing passes
    tight_config = InvestmentConfig(
        criteria=ScreeningCriteria(max_price=1, min_beds=10, max_dom=0),
        financial_assumptions=FinancialAssumptions(),
        output=OutputConfig(market="Austin, TX", max_shortlist=5),
    )

    with patch("pipeline.anthropic.AsyncAnthropic") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client_cls.return_value = mock_client

        shortlist = await run("Austin, TX", tight_config)

    assert shortlist.deals == []
    assert "No listings matched" in shortlist.run_summary
    mock_client.messages.create.assert_not_called()


# ── All enrichments fail → pipeline continues ──────────────────────────────────

@pytest.mark.asyncio
async def test_pipeline_all_enrichment_fails_continues(tmp_path):
    """
    Chaos test: WalkScore times out for all listings.
    Pipeline should still analyze and rank (walk_score=None, from fixtures).
    """
    from pipeline import run

    with (
        patch("tools.enrich._fetch_walk_score", return_value=None),
        patch("pipeline.anthropic.AsyncAnthropic") as mock_client_cls,
    ):
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(
            return_value=_mock_claude_response(_MOCK_SHORTLIST)
        )
        mock_client_cls.return_value = mock_client

        shortlist = await run("Austin, TX", CONFIG)

    assert len(shortlist.deals) >= 1


# ── Claude API error → SystemExit with saved data ─────────────────────────────

@pytest.mark.asyncio
async def test_pipeline_claude_error_saves_analyzed_data(tmp_path, monkeypatch):
    """
    If the Anthropic API fails, analyzed results are saved to disk and
    SystemExit(1) is raised.
    """
    import anthropic
    from pipeline import run, _OUTPUTS_DIR

    # Redirect outputs to tmp_path for this test
    monkeypatch.setattr("pipeline._OUTPUTS_DIR", tmp_path)

    with patch("pipeline.anthropic.AsyncAnthropic") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(
            side_effect=anthropic.APIConnectionError(request=MagicMock())
        )
        mock_client_cls.return_value = mock_client

        with pytest.raises(SystemExit) as exc_info:
            await run("Austin, TX", CONFIG)

    assert exc_info.value.code == 1

    # Analyzed data should have been saved before the Claude call
    analyzed_files = list(tmp_path.glob("*_analyzed.json"))
    assert len(analyzed_files) == 1

    data = json.loads(analyzed_files[0].read_text())
    assert isinstance(data, list)
    assert len(data) > 0


# ── Output JSON file is written ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_pipeline_writes_output_json(tmp_path, monkeypatch):
    from pipeline import run

    monkeypatch.setattr("pipeline._OUTPUTS_DIR", tmp_path)

    with patch("pipeline.anthropic.AsyncAnthropic") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(
            return_value=_mock_claude_response(_MOCK_SHORTLIST)
        )
        mock_client_cls.return_value = mock_client

        await run("Austin, TX", CONFIG)

    output_files = [f for f in tmp_path.glob("*.json") if "analyzed" not in f.name]
    assert len(output_files) == 1

    data = json.loads(output_files[0].read_text())
    assert data["market"] == "Austin, TX"
    assert "deals" in data


# ── Hostile QA: all None fields ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_pipeline_all_none_fields_no_crash(tmp_path, monkeypatch):
    """
    All listings have price=None, beds=None, dom=None.
    Pipeline must exit gracefully with "no matches" — no crash, no Claude call.
    """
    from pipeline import run
    from tools.fetch import fetch_listings
    from tools.models import RawListing

    monkeypatch.setattr("pipeline._OUTPUTS_DIR", tmp_path)

    broken_listings = [
        RawListing(zpid=str(i), address=f"Broken {i}", price=None, beds=None, days_on_market=None)
        for i in range(5)
    ]

    with (
        patch("pipeline.fetch_listings", return_value=broken_listings),
        patch("pipeline.anthropic.AsyncAnthropic") as mock_client_cls,
    ):
        mock_client = AsyncMock()
        mock_client_cls.return_value = mock_client

        shortlist = await run("Austin, TX", CONFIG)

    assert shortlist.deals == []
    mock_client.messages.create.assert_not_called()
