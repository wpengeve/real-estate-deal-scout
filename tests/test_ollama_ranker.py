"""
Unit tests for tools/ollama_ranker.py.

No network calls — Ollama HTTP is mocked throughout.
"""
import json
from unittest.mock import MagicMock, patch

import pytest

from tools.models import (
    FinancialResult,
    FlaggedListing,
    InvestmentConfig,
    OutputConfig,
    RawListing,
    RiskLevel,
    RiskResult,
    ScreeningCriteria,
    FinancialAssumptions,
)
from tools.ollama_ranker import (
    _build_listings_data,
    _build_prompt,
    _make_deal,
    _parse_response,
    _top_candidates,
    ollama_rank_and_narrate,
)

# ── Fixtures ───────────────────────────────────────────────────────────────────

CONFIG = InvestmentConfig(
    criteria=ScreeningCriteria(max_price=1_000_000, min_beds=3, max_dom=30),
    financial_assumptions=FinancialAssumptions(),
    output=OutputConfig(market="Seattle, WA", max_shortlist=2, ranker="ollama"),
)


def _flagged(
    address: str = "123 Main St, Seattle, WA 98101",
    price: float = 500_000,
    cap_rate: float = 0.05,
    cashflow: float = 200.0,
    risk: RiskLevel = RiskLevel.LOW,
) -> FlaggedListing:
    listing = RawListing(
        zpid="test",
        address=address,
        price=price,
        beds=3,
        days_on_market=10,
    )
    financials = FinancialResult(
        success=True,
        cap_rate=cap_rate,
        coc_return=0.08,
        monthly_cashflow=cashflow,
    )
    risks = RiskResult(overall_risk=risk)
    return FlaggedListing(listing=listing, financials=financials, risks=risks)


# ── _top_candidates ────────────────────────────────────────────────────────────

def test_top_candidates_sorted_by_cap_rate():
    low = _flagged(address="A", cap_rate=0.03)
    high = _flagged(address="B", cap_rate=0.07)
    mid = _flagged(address="C", cap_rate=0.05)
    result = _top_candidates([low, high, mid], n=3)
    assert [f.listing.address for f in result] == ["B", "C", "A"]


def test_top_candidates_high_risk_pushed_to_back():
    low_risk = _flagged(address="A", cap_rate=0.04, risk=RiskLevel.LOW)
    high_risk = _flagged(address="B", cap_rate=0.06, risk=RiskLevel.HIGH)
    result = _top_candidates([high_risk, low_risk], n=2)
    assert result[0].listing.address == "A"
    assert result[1].listing.address == "B"


def test_top_candidates_respects_n():
    listings = [_flagged(address=str(i), cap_rate=float(i) / 100) for i in range(10)]
    result = _top_candidates(listings, n=3)
    assert len(result) == 3


def test_top_candidates_none_cap_rate_goes_last():
    good = _flagged(address="A", cap_rate=0.05)
    no_data = _flagged(address="B", cap_rate=None)
    no_data.financials.cap_rate = None
    result = _top_candidates([no_data, good], n=2)
    assert result[0].listing.address == "A"


# ── _parse_response ────────────────────────────────────────────────────────────

def _make_response_json(addresses: list[str], summary: str = "summary") -> str:
    return json.dumps({
        "run_summary": summary,
        "deals": [
            {"rank": i + 1, "address": addr, "narrative": f"narrative for {addr}"}
            for i, addr in enumerate(addresses)
        ],
    })


def test_parse_response_returns_correct_addresses():
    addr = "123 Main St, Seattle, WA 98101"
    candidates = [_flagged(address=addr)]
    content = _make_response_json([addr])
    shortlist = _parse_response(content, candidates, CONFIG)
    assert len(shortlist.deals) == 1
    assert shortlist.deals[0].address == addr


def test_parse_response_discards_hallucinated_address():
    real_addr = "123 Main St, Seattle, WA 98101"
    candidates = [_flagged(address=real_addr), _flagged(address="456 Oak Ave, Seattle, WA 98102")]
    # Model returns one hallucinated address and one real one
    content = _make_response_json(["9999999", real_addr])
    shortlist = _parse_response(content, candidates, CONFIG)
    assert all(d.address != "9999999" for d in shortlist.deals)
    assert any(d.address == real_addr for d in shortlist.deals)


def test_parse_response_fills_missing_slots_from_candidates():
    """If model returns fewer valid addresses than max_shortlist, fill from candidates."""
    addr1 = "123 Main St, Seattle, WA 98101"
    addr2 = "456 Oak Ave, Seattle, WA 98102"
    candidates = [_flagged(address=addr1, cap_rate=0.06), _flagged(address=addr2, cap_rate=0.04)]
    # Model only returns one valid address
    content = _make_response_json([addr1])
    shortlist = _parse_response(content, candidates, CONFIG)
    assert len(shortlist.deals) == 2
    assert shortlist.deals[1].address == addr2


def test_parse_response_no_duplicate_addresses():
    addr = "123 Main St, Seattle, WA 98101"
    candidates = [_flagged(address=addr), _flagged(address="456 Oak Ave, Seattle, WA 98102")]
    # Model returns the same address twice
    content = _make_response_json([addr, addr])
    shortlist = _parse_response(content, candidates, CONFIG)
    addresses = [d.address for d in shortlist.deals]
    assert len(addresses) == len(set(addresses))


def test_parse_response_invalid_json_raises():
    with pytest.raises(RuntimeError, match="invalid JSON"):
        _parse_response("not json at all", [], CONFIG)


def test_parse_response_uses_run_summary():
    addr = "123 Main St, Seattle, WA 98101"
    candidates = [_flagged(address=addr)]
    content = _make_response_json([addr], summary="Great deals found")
    shortlist = _parse_response(content, candidates, CONFIG)
    assert shortlist.run_summary == "Great deals found"


# ── _make_deal ─────────────────────────────────────────────────────────────────

def test_make_deal_uses_pipeline_financials_not_model():
    """Financial data must always come from the pipeline, never from the model."""
    f = _flagged(cap_rate=0.055, cashflow=300.0)
    deal = _make_deal(1, f, "Some narrative", CONFIG)
    assert deal.cap_rate == pytest.approx(0.055)
    assert deal.monthly_cashflow == pytest.approx(300.0)


def test_make_deal_fallback_narrative_when_empty():
    f = _flagged(cap_rate=0.04, cashflow=-100.0)
    deal = _make_deal(1, f, "", CONFIG)
    assert len(deal.narrative) > 0
    assert "Cap rate" in deal.narrative


# ── ollama_rank_and_narrate (integration with mock HTTP) ───────────────────────

def _mock_ollama_response(addresses: list[str]) -> MagicMock:
    resp = MagicMock()
    resp.json.return_value = {
        "message": {
            "content": _make_response_json(addresses, summary="Top deals")
        }
    }
    resp.raise_for_status = MagicMock()
    return resp


def test_ollama_rank_and_narrate_returns_shortlist():
    addr = "123 Main St, Seattle, WA 98101"
    flagged = [_flagged(address=addr)]

    with patch("tools.ollama_ranker.httpx.post", return_value=_mock_ollama_response([addr])):
        shortlist = ollama_rank_and_narrate(flagged, CONFIG)

    assert shortlist.market == "Seattle, WA"
    assert len(shortlist.deals) >= 1


def test_ollama_rank_and_narrate_raises_on_connect_error():
    import httpx
    flagged = [_flagged()]
    with patch("tools.ollama_ranker.httpx.post", side_effect=httpx.ConnectError("refused")):
        with pytest.raises(RuntimeError, match="Cannot connect to Ollama"):
            ollama_rank_and_narrate(flagged, CONFIG)


def test_ollama_rank_and_narrate_raises_on_bad_json():
    resp = MagicMock()
    resp.json.return_value = {"message": {"content": "not json"}}
    resp.raise_for_status = MagicMock()
    flagged = [_flagged()]
    with patch("tools.ollama_ranker.httpx.post", return_value=resp):
        with pytest.raises(RuntimeError, match="invalid JSON"):
            ollama_rank_and_narrate(flagged, CONFIG)
