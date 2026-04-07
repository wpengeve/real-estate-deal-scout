"""
Conversational investment criteria intake using Claude tool-use.

Flow:
  1. User describes what they're looking for (plain English)
  2. Claude extracts structured criteria and calls set_investment_criteria tool
  3. Claude summarizes criteria back for confirmation
  4. User types 'done' to confirm → InvestmentConfig returned
  5. Or user refines → Claude updates criteria → repeat

Entry point: run_chat_intake(base_config) → InvestmentConfig | None
"""
import os

from anthropic import Anthropic

from tools.models import (
    FinancialAssumptions,
    InvestmentConfig,
    OutputConfig,
    ScreeningCriteria,
)

_MODEL = "claude-sonnet-4-6"

_TOOL_DEF: dict = {
    "name": "set_investment_criteria",
    "description": (
        "Record the user's investment search criteria. Call this once you have "
        "enough information to fill in max_price, min_beds, at least one city, "
        "and down_payment_pct. Re-call if the user requests changes."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "max_price": {
                "type": "number",
                "description": "Maximum purchase price in USD (e.g. 1500000 for $1.5M)",
            },
            "min_beds": {
                "type": "integer",
                "description": "Minimum number of bedrooms",
            },
            "min_baths": {
                "type": "number",
                "description": (
                    "Minimum number of bathrooms (e.g. 2 or 2.5). Use 2.0 when the user "
                    "asks for a primary/master suite with an en-suite bathroom, or says "
                    "'at least 2 baths'. Omit if not mentioned."
                ),
            },
            "allowed_cities": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Cities to include. When a metro area is mentioned, include the "
                    "core city and suburbs. Example: 'Seattle metro' → "
                    "['Seattle', 'Bellevue', 'Kirkland', 'Redmond', 'Shoreline', "
                    "'Bothell', 'Kenmore', 'Mountlake Terrace', 'Lynnwood', 'Edmonds', "
                    "'Renton', 'Burien', 'Tukwila', 'Mercer Island', 'Issaquah', 'Sammamish']"
                ),
            },
            "market_name": {
                "type": "string",
                "description": "Display name for the market, e.g. 'Seattle, WA'",
            },
            "down_payment_pct": {
                "type": "number",
                "description": "Down payment as decimal (e.g. 0.20 for 20%, 0.25 for 25%)",
            },
            "loan_rate_annual": {
                "type": "number",
                "description": (
                    "Annual mortgage rate as decimal (e.g. 0.065 for 6.5%). "
                    "Use 0.0525 if not specified."
                ),
            },
            "max_hoa_fee": {
                "type": ["number", "null"],
                "description": (
                    "Max monthly HOA fee in USD. "
                    "Use 0 to require no HOA. Use null for no restriction."
                ),
            },
            "preferred_home_types": {
                "type": ["array", "null"],
                "items": {
                    "type": "string",
                    "enum": ["Single Family", "Condo", "Townhouse", "Multi-Family"],
                },
                "description": "Preferred property types. Use null if no preference.",
            },
            "target_cap_rate": {
                "type": "number",
                "description": "Target cap rate as decimal (e.g. 0.05 for 5%). Default: 0.05.",
            },
            "min_cap_rate": {
                "type": ["number", "null"],
                "description": (
                    "Minimum acceptable cap rate. For high-cost markets like Seattle, "
                    "0.02 is realistic. Use null if no filter."
                ),
            },
            "require_primary_suite": {
                "type": "boolean",
                "description": (
                    "Set true when user asks for a master bedroom, primary suite, "
                    "or en-suite bedroom. Filters out listings confirmed to lack one."
                ),
            },
            "max_shortlist": {
                "type": "integer",
                "description": "Number of top deals to show. Default: 15.",
            },
        },
        "required": ["max_price", "min_beds", "allowed_cities", "market_name", "down_payment_pct"],
    },
}

_SYSTEM_PROMPT = """\
You are a real estate investment advisor helping users set up their property search criteria.

Goal: understand what they're looking for, then call set_investment_criteria with the structured criteria.

Ask follow-up questions ONLY if you're missing any required fields: max_price, min_beds, \
target cities, or down_payment_pct.

For optional fields (loan rate, HOA restriction, property type), use sensible defaults \
if not mentioned — don't interrogate the user.

After calling set_investment_criteria, summarize the criteria in 3-4 bullet points \
and tell the user to type 'done' to confirm or describe any changes.

Be concise — investors are busy.\
"""


def run_chat_intake(base_config: InvestmentConfig) -> InvestmentConfig | None:
    """
    Run an interactive CLI conversation to collect investment criteria from the user.

    Uses Claude with tool-use to extract structured InvestmentConfig from natural language.
    The conversation continues until the user types 'done' to confirm or 'cancel' to abort.

    Returns:
        Updated InvestmentConfig if user confirms, None if user cancels or API key is missing.
    """
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return None

    client = Anthropic(api_key=api_key)
    messages: list[dict] = []
    extracted: dict | None = None

    print()
    print("─" * 60)
    print("  Chat Setup — describe your investment criteria")
    print("  type 'done' to confirm · 'cancel' to exit")
    print("─" * 60)
    print()
    print("Agent: What kind of investment property are you looking for?\n")

    while True:
        try:
            user_input = input("You: ").strip()
        except (KeyboardInterrupt, EOFError):
            print()
            return None

        if not user_input:
            continue

        if user_input.lower() == "cancel":
            return None

        if user_input.lower() == "done":
            if extracted:
                break
            print("Agent: Please describe your investment criteria first.\n")
            continue

        messages.append({"role": "user", "content": user_input})
        result = _call_claude(client, messages)
        messages = result["messages"]

        if result["tool_input"] is not None:
            extracted = result["tool_input"]
            # Return tool result so Claude can continue the conversation
            messages.append({
                "role": "user",
                "content": [{
                    "type": "tool_result",
                    "tool_use_id": result["tool_use_id"],
                    "content": "Criteria recorded.",
                }],
            })
            # Get Claude's confirmation summary
            confirm = _call_claude(client, messages)
            messages = confirm["messages"]
            for text in confirm["texts"]:
                print(f"Agent: {text}\n")
        else:
            for text in result["texts"]:
                print(f"Agent: {text}\n")

    return _build_config(extracted, base_config)  # type: ignore[arg-type]


def _call_claude(client: Anthropic, messages: list[dict]) -> dict:
    """
    Send messages to Claude and return parsed response.

    Returns a dict with:
        texts: list[str]       — text blocks from the response
        tool_input: dict|None  — extracted criteria if Claude called the tool
        tool_use_id: str|None  — tool use block ID (needed for tool_result)
        messages: list[dict]   — updated messages list (with assistant turn appended)
    """
    response = client.messages.create(
        model=_MODEL,
        max_tokens=1024,
        system=_SYSTEM_PROMPT,
        tools=[_TOOL_DEF],
        messages=messages,
    )

    texts: list[str] = []
    tool_input: dict | None = None
    tool_use_id: str | None = None

    for block in response.content:
        if block.type == "text":
            texts.append(block.text)
        elif block.type == "tool_use" and block.name == "set_investment_criteria":
            tool_input = block.input
            tool_use_id = block.id

    messages.append({"role": "assistant", "content": response.content})
    return {
        "texts": texts,
        "tool_input": tool_input,
        "tool_use_id": tool_use_id,
        "messages": messages,
    }


def _build_config(extracted: dict, base: InvestmentConfig) -> InvestmentConfig:
    """
    Merge Claude-extracted criteria into the base pipeline config.

    Fields not extracted by Claude fall back to base_config values so that
    advanced settings (vacancy rate, maintenance %, etc.) are preserved.
    """
    fa = base.financial_assumptions
    return InvestmentConfig(
        fetch=base.fetch,
        enrich=base.enrich,
        criteria=ScreeningCriteria(
            max_price=extracted["max_price"],
            min_beds=extracted["min_beds"],
            max_dom=base.criteria.max_dom,
            target_cap_rate=extracted.get("target_cap_rate", base.criteria.target_cap_rate),
            walkscore_min=base.criteria.walkscore_min,
            dom_outlier_multiplier=base.criteria.dom_outlier_multiplier,
            min_baths=extracted.get("min_baths"),
            require_primary_suite=extracted.get("require_primary_suite", False),
            max_hoa_fee=extracted.get("max_hoa_fee"),
            min_cap_rate=extracted.get("min_cap_rate"),
            preferred_home_types=extracted.get("preferred_home_types"),
            allowed_cities=extracted["allowed_cities"],
        ),
        financial_assumptions=FinancialAssumptions(
            down_payment_pct=extracted["down_payment_pct"],
            loan_rate_annual=extracted.get("loan_rate_annual", fa.loan_rate_annual),
            loan_term_years=fa.loan_term_years,
            vacancy_rate=fa.vacancy_rate,
            management_fee_pct=fa.management_fee_pct,
            maintenance_pct_of_value=fa.maintenance_pct_of_value,
            insurance_annual=fa.insurance_annual,
            closing_cost_pct=fa.closing_cost_pct,
            property_tax_rate_pct=fa.property_tax_rate_pct,
        ),
        output=OutputConfig(
            max_shortlist=extracted.get("max_shortlist", base.output.max_shortlist),
            market=extracted["market_name"],
            ranker=base.output.ranker,
            ollama_model=base.output.ollama_model,
            ollama_base_url=base.output.ollama_base_url,
        ),
    )