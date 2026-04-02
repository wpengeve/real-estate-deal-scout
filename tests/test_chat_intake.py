"""
Tests for tools/chat_intake.py — conversational InvestmentConfig extraction.
"""
from unittest.mock import MagicMock, patch

import pytest

from tools.chat_intake import _build_config, _call_claude, run_chat_intake
from tools.models import (
    EnrichConfig,
    FetchConfig,
    FinancialAssumptions,
    InvestmentConfig,
    OutputConfig,
    ScreeningCriteria,
)


# ─── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def base_config() -> InvestmentConfig:
    return InvestmentConfig(
        fetch=FetchConfig(data_source="csv"),
        enrich=EnrichConfig(hud_rent_multiplier=1.0),
        criteria=ScreeningCriteria(
            max_price=2_000_000,
            min_beds=3,
            max_dom=9999,
            target_cap_rate=0.05,
            walkscore_min=0,
            max_hoa_fee=0,
            min_cap_rate=0.02,
            preferred_home_types=["Single Family"],
            allowed_cities=["Seattle", "Bellevue"],
        ),
        financial_assumptions=FinancialAssumptions(
            down_payment_pct=0.25,
            loan_rate_annual=0.0525,
            loan_term_years=30,
            vacancy_rate=0.08,
            management_fee_pct=0.10,
            maintenance_pct_of_value=0.01,
            insurance_annual=1200.0,
            closing_cost_pct=0.03,
            property_tax_rate_pct=0.012,
        ),
        output=OutputConfig(
            max_shortlist=15,
            market="Seattle, WA",
            ranker="ollama",
            ollama_model="llama3.1:8b",
        ),
    )


def _make_text_block(text: str) -> MagicMock:
    block = MagicMock()
    block.type = "text"
    block.text = text
    return block


def _make_tool_block(tool_input: dict, tool_use_id: str = "tu_123") -> MagicMock:
    block = MagicMock()
    block.type = "tool_use"
    block.name = "set_investment_criteria"
    block.input = tool_input
    block.id = tool_use_id
    return block


def _make_response(*blocks) -> MagicMock:
    resp = MagicMock()
    resp.content = list(blocks)
    return resp


# ─── _build_config ─────────────────────────────────────────────────────────────

class TestBuildConfig:
    def test_required_fields_applied(self, base_config):
        extracted = {
            "max_price": 1_500_000,
            "min_beds": 4,
            "allowed_cities": ["Portland", "Beaverton"],
            "market_name": "Portland, OR",
            "down_payment_pct": 0.20,
        }
        result = _build_config(extracted, base_config)

        assert result.criteria.max_price == 1_500_000
        assert result.criteria.min_beds == 4
        assert result.criteria.allowed_cities == ["Portland", "Beaverton"]
        assert result.output.market == "Portland, OR"
        assert result.financial_assumptions.down_payment_pct == 0.20

    def test_optional_fields_from_extracted(self, base_config):
        extracted = {
            "max_price": 1_000_000,
            "min_beds": 2,
            "allowed_cities": ["Austin"],
            "market_name": "Austin, TX",
            "down_payment_pct": 0.25,
            "loan_rate_annual": 0.070,
            "max_hoa_fee": 300.0,
            "preferred_home_types": ["Condo", "Townhouse"],
            "target_cap_rate": 0.06,
            "min_cap_rate": 0.04,
            "max_shortlist": 10,
        }
        result = _build_config(extracted, base_config)

        assert result.financial_assumptions.loan_rate_annual == 0.070
        assert result.criteria.max_hoa_fee == 300.0
        assert result.criteria.preferred_home_types == ["Condo", "Townhouse"]
        assert result.criteria.target_cap_rate == 0.06
        assert result.criteria.min_cap_rate == 0.04
        assert result.output.max_shortlist == 10

    def test_optional_fields_fall_back_to_base(self, base_config):
        extracted = {
            "max_price": 800_000,
            "min_beds": 3,
            "allowed_cities": ["Seattle"],
            "market_name": "Seattle, WA",
            "down_payment_pct": 0.25,
            # No loan_rate_annual, max_hoa_fee, preferred_home_types, etc.
        }
        result = _build_config(extracted, base_config)

        # Falls back to base
        assert result.financial_assumptions.loan_rate_annual == base_config.financial_assumptions.loan_rate_annual
        assert result.criteria.max_hoa_fee is None   # extracted.get("max_hoa_fee") → None
        assert result.criteria.preferred_home_types is None
        assert result.output.max_shortlist == base_config.output.max_shortlist

    def test_base_infrastructure_preserved(self, base_config):
        extracted = {
            "max_price": 1_000_000,
            "min_beds": 2,
            "allowed_cities": ["Seattle"],
            "market_name": "Seattle, WA",
            "down_payment_pct": 0.20,
        }
        result = _build_config(extracted, base_config)

        assert result.fetch == base_config.fetch
        assert result.enrich == base_config.enrich
        assert result.financial_assumptions.vacancy_rate == base_config.financial_assumptions.vacancy_rate
        assert result.financial_assumptions.management_fee_pct == base_config.financial_assumptions.management_fee_pct
        assert result.output.ranker == base_config.output.ranker

    def test_null_optional_fields_accepted(self, base_config):
        """Claude may explicitly pass null for max_hoa_fee or preferred_home_types."""
        extracted = {
            "max_price": 1_200_000,
            "min_beds": 3,
            "allowed_cities": ["Seattle"],
            "market_name": "Seattle, WA",
            "down_payment_pct": 0.25,
            "max_hoa_fee": None,
            "preferred_home_types": None,
            "min_cap_rate": None,
        }
        result = _build_config(extracted, base_config)

        assert result.criteria.max_hoa_fee is None
        assert result.criteria.preferred_home_types is None
        assert result.criteria.min_cap_rate is None


# ─── _call_claude ──────────────────────────────────────────────────────────────

class TestCallClaude:
    def test_returns_text_blocks(self):
        client = MagicMock()
        client.messages.create.return_value = _make_response(
            _make_text_block("Tell me more about your budget.")
        )
        messages: list[dict] = []

        result = _call_claude(client, messages)

        assert result["texts"] == ["Tell me more about your budget."]
        assert result["tool_input"] is None
        assert result["tool_use_id"] is None
        # Assistant turn appended
        assert len(result["messages"]) == 1
        assert result["messages"][0]["role"] == "assistant"

    def test_returns_tool_input(self):
        tool_data = {
            "max_price": 1_500_000,
            "min_beds": 3,
            "allowed_cities": ["Seattle"],
            "market_name": "Seattle, WA",
            "down_payment_pct": 0.25,
        }
        client = MagicMock()
        client.messages.create.return_value = _make_response(
            _make_text_block("I've captured your criteria."),
            _make_tool_block(tool_data, "tu_abc"),
        )
        messages: list[dict] = [{"role": "user", "content": "Looking in Seattle, $1.5M, 3 beds, 25% down"}]

        result = _call_claude(client, messages)

        assert result["tool_input"] == tool_data
        assert result["tool_use_id"] == "tu_abc"
        assert "I've captured your criteria." in result["texts"]

    def test_ignores_unknown_tool_blocks(self):
        unknown_tool = MagicMock()
        unknown_tool.type = "tool_use"
        unknown_tool.name = "some_other_tool"

        client = MagicMock()
        client.messages.create.return_value = _make_response(
            _make_text_block("Here you go."),
            unknown_tool,
        )
        result = _call_claude(client, [])

        assert result["tool_input"] is None
        assert result["texts"] == ["Here you go."]

    def test_messages_accumulated(self):
        client = MagicMock()
        client.messages.create.return_value = _make_response(
            _make_text_block("What's your budget?")
        )
        messages = [{"role": "user", "content": "Hello"}]
        result = _call_claude(client, messages)

        assert len(result["messages"]) == 2
        assert result["messages"][0]["role"] == "user"
        assert result["messages"][1]["role"] == "assistant"


# ─── run_chat_intake ───────────────────────────────────────────────────────────

class TestRunChatIntake:
    def test_returns_none_without_api_key(self, base_config, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        result = run_chat_intake(base_config)
        assert result is None

    def test_returns_none_on_cancel(self, base_config, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        with patch("tools.chat_intake.Anthropic"), \
             patch("builtins.input", side_effect=["cancel"]):
            result = run_chat_intake(base_config)
        assert result is None

    def test_returns_none_on_keyboard_interrupt(self, base_config, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        with patch("tools.chat_intake.Anthropic"), \
             patch("builtins.input", side_effect=KeyboardInterrupt):
            result = run_chat_intake(base_config)
        assert result is None

    def test_done_before_criteria_prompts_again(self, base_config, monkeypatch, capsys):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        # "done" first (no criteria yet) → then "cancel" to exit
        with patch("tools.chat_intake.Anthropic"), \
             patch("builtins.input", side_effect=["done", "cancel"]):
            result = run_chat_intake(base_config)

        captured = capsys.readouterr()
        assert "describe your investment criteria first" in captured.out.lower()
        assert result is None

    def test_full_flow_single_turn(self, base_config, monkeypatch):
        """User describes criteria in one message → tool called → user types 'done'."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

        tool_data = {
            "max_price": 1_200_000,
            "min_beds": 3,
            "allowed_cities": ["Seattle", "Bellevue", "Kirkland"],
            "market_name": "Seattle, WA",
            "down_payment_pct": 0.25,
            "loan_rate_annual": 0.065,
            "max_hoa_fee": 0,
            "preferred_home_types": ["Single Family"],
        }

        # First call: tool + text; second call: confirmation text
        first_response = _make_response(
            _make_tool_block(tool_data, "tu_001"),
            _make_text_block("Captured."),
        )
        confirm_response = _make_response(
            _make_text_block(
                "• Max price $1.2M, 3+ beds, Single Family only\n"
                "• 25% down, 6.5% rate\n"
                "• No HOA\n"
                "Type 'done' to confirm."
            )
        )

        mock_client = MagicMock()
        mock_client.messages.create.side_effect = [first_response, confirm_response]

        with patch("tools.chat_intake.Anthropic", return_value=mock_client), \
             patch("builtins.input", side_effect=[
                 "Looking for SF in Seattle metro, max $1.2M, 3 beds, 25% down, no HOA",
                 "done",
             ]):
            result = run_chat_intake(base_config)

        assert result is not None
        assert result.criteria.max_price == 1_200_000
        assert result.criteria.min_beds == 3
        assert result.criteria.allowed_cities == ["Seattle", "Bellevue", "Kirkland"]
        assert result.criteria.max_hoa_fee == 0
        assert result.financial_assumptions.down_payment_pct == 0.25
        assert result.financial_assumptions.loan_rate_annual == 0.065
        assert result.output.market == "Seattle, WA"

    def test_multi_turn_refinement(self, base_config, monkeypatch):
        """User provides criteria, then refines → tool called again with updated values."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

        first_tool_data = {
            "max_price": 1_000_000,
            "min_beds": 2,
            "allowed_cities": ["Seattle"],
            "market_name": "Seattle, WA",
            "down_payment_pct": 0.20,
        }
        second_tool_data = {
            "max_price": 1_500_000,
            "min_beds": 3,
            "allowed_cities": ["Seattle", "Bellevue"],
            "market_name": "Seattle, WA",
            "down_payment_pct": 0.25,
        }

        r1 = _make_response(_make_tool_block(first_tool_data, "tu_1"))
        r2 = _make_response(_make_text_block("Got it — $1M, 2 beds. Type 'done' to confirm."))
        r3 = _make_response(_make_tool_block(second_tool_data, "tu_2"))
        r4 = _make_response(_make_text_block("Updated — $1.5M, 3 beds. Type 'done' to confirm."))

        mock_client = MagicMock()
        mock_client.messages.create.side_effect = [r1, r2, r3, r4]

        with patch("tools.chat_intake.Anthropic", return_value=mock_client), \
             patch("builtins.input", side_effect=[
                 "Seattle, $1M, 2 beds, 20% down",
                 "actually make it $1.5M, 3 beds, 25% down, add Bellevue",
                 "done",
             ]):
            result = run_chat_intake(base_config)

        assert result is not None
        assert result.criteria.max_price == 1_500_000
        assert result.criteria.min_beds == 3
        assert result.criteria.allowed_cities == ["Seattle", "Bellevue"]
        assert result.financial_assumptions.down_payment_pct == 0.25

    def test_empty_input_skipped(self, base_config, monkeypatch):
        """Empty input lines are ignored without calling the API."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

        tool_data = {
            "max_price": 900_000,
            "min_beds": 3,
            "allowed_cities": ["Renton"],
            "market_name": "Seattle, WA",
            "down_payment_pct": 0.20,
        }
        r1 = _make_response(_make_tool_block(tool_data))
        r2 = _make_response(_make_text_block("Looks good. Type 'done' to confirm."))

        mock_client = MagicMock()
        mock_client.messages.create.side_effect = [r1, r2]

        with patch("tools.chat_intake.Anthropic", return_value=mock_client), \
             patch("builtins.input", side_effect=["", "  ", "Renton, $900K, 3 beds, 20%", "done"]):
            result = run_chat_intake(base_config)

        assert result is not None
        # Only 2 API calls (not 4) — empty inputs were skipped
        assert mock_client.messages.create.call_count == 2