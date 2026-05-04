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
            "min_price": {
                "type": ["number", "null"],
                "description": (
                    "Minimum purchase price in USD. Set when user gives a price range "
                    "(e.g. '$1M–$1.65M' → min_price=1000000). Omit if no lower bound."
                ),
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
                    "Minimum acceptable cap rate as decimal (e.g. 0.03 for 3%). "
                    "Only set this if the user explicitly asks for a minimum cap rate. "
                    "Default: null (no filter). Do NOT infer or apply automatically."
                ),
            },
            "require_primary_suite": {
                "type": "boolean",
                "description": (
                    "Set true when user asks for a master bedroom, primary suite, "
                    "or en-suite bedroom. Filters out listings confirmed to lack one."
                ),
            },
            "max_year_built": {
                "type": ["integer", "null"],
                "description": (
                    "Exclude homes built after this year. Use when user says "
                    "'no new construction' (set to 2019) or gives a specific cutoff. "
                    "Omit if not mentioned."
                ),
            },
            "min_school_score": {
                "type": ["number", "null"],
                "description": (
                    "Minimum school proficiency score 0–100. Maps from GreatSchools-style "
                    "ratings: 6/10 → 55, 7/10 → 65, 8/10 → 75. Set when user mentions "
                    "school rating or school zone requirements. Omit if not mentioned."
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

Workflow:
1. Ask the user focused questions to collect all required fields. Ask ONE question at a time.
   Required: max_price, min_beds, target city/market, down_payment_pct.
2. Once you have all required fields, call set_investment_criteria.
3. After calling the tool, confirm by saying something like:
   "Got it — here's what I'll search for: [2-3 key criteria]. Click **Find Deals** to start, \
or tell me if anything needs changing."

Rules:
- Do NOT call the tool until you have all required fields from the user.
- Do NOT ask about optional fields (loan rate, HOA, property type) unless the user brings them up.
- Use sensible defaults for optional fields: loan rate 5.25%, no HOA restriction, any type.
- For a metro area, include the major city plus suburbs in allowed_cities.
- Be concise — investors are busy.
- If the user mentions a specific property address or asks to analyze a specific listing, \
do NOT try to look it up or ask for manual details. Instead reply exactly: \
"Paste the Redfin listing URL for that property and I'll analyze it instantly."\
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

    If extracted contains scraperapi_search_urls, those replace the base fetch
    URLs and data_source is forced to "scraperapi" so the new market is fetched.
    """
    from tools.models import FetchConfig  # avoid circular import at module level

    fa = base.financial_assumptions

    # Determine fetch config.
    # If explicit search URLs were provided, use them.
    # If the market changed from the base config's market, clear the base URLs so the
    # pipeline's auto-resolver fetches URLs for the correct city via Redfin autocomplete.
    # Otherwise (same market), reuse the base fetch config as-is.
    scraperapi_urls = extracted.get("scraperapi_search_urls") or []
    market_changed = (
        extracted["market_name"].strip().lower() != base.output.market.strip().lower()
    )
    if scraperapi_urls:
        fetch = FetchConfig(
            data_source="scraperapi",
            csv_path=base.fetch.csv_path,
            csv_paths=base.fetch.csv_paths,
            redfin_region_id=base.fetch.redfin_region_id,
            redfin_region_type=base.fetch.redfin_region_type,
            redfin_max_homes=base.fetch.redfin_max_homes,
            scraperapi_search_urls=scraperapi_urls,
        )
    elif market_changed and base.fetch.data_source == "scraperapi":
        # Different market — clear pre-configured URLs so pipeline auto-resolves
        fetch = base.fetch.model_copy(update={"scraperapi_search_urls": []})
    else:
        fetch = base.fetch

    return InvestmentConfig(
        fetch=fetch,
        enrich=base.enrich,
        criteria=ScreeningCriteria(
            max_price=extracted["max_price"],
            min_price=extracted.get("min_price"),
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
            max_year_built=extracted.get("max_year_built"),
            min_school_score=extracted.get("min_school_score"),
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