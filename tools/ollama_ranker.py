"""
Ollama-based ranking and narration — free local LLM alternative to Claude.

Calls a locally running Ollama server (default: http://localhost:11434).
Uses the /api/chat endpoint with format="json" for structured output.

Design:
  - Pre-sorts listings by cap rate and sends only the top N candidates to the
    model (not all 184) — reduces the problem space for smaller models.
  - Always uses real financial data from the pipeline; the model only supplies
    ranking order and narrative text.
  - Validates addresses strictly — hallucinated addresses are discarded and
    replaced with the next-best real candidate.

Setup:
  1. Download Ollama from ollama.com
  2. Run: ollama pull llama3.1:8b
  3. Set ranker: ollama and ollama_model: llama3.1:8b in config.yaml
"""
import json
import logging

import httpx

from tools.models import DealNarrative, FlaggedListing, InvestmentConfig, Shortlist

logger = logging.getLogger(__name__)

_OLLAMA_TIMEOUT = 180.0   # local inference can be slow on first run
_CANDIDATES = 20          # send only the top N pre-sorted listings to the LLM


def ollama_rank_and_narrate(
    flagged: list[FlaggedListing],
    config: InvestmentConfig,
) -> Shortlist:
    """
    Use a local Ollama model to rank and narrate the top deals.

    Raises RuntimeError if Ollama is unreachable or returns unparseable output.
    """
    base_url = config.output.ollama_base_url.rstrip("/")
    model = config.output.ollama_model

    # Pre-sort by cap rate descending, take top candidates only
    candidates = _top_candidates(flagged, n=_CANDIDATES)
    listings_data = _build_listings_data(candidates)
    prompt = _build_prompt(listings_data, config)

    try:
        response = httpx.post(
            f"{base_url}/api/chat",
            json={
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "format": "json",
                "stream": False,
                "options": {"temperature": 0.2},
            },
            timeout=_OLLAMA_TIMEOUT,
        )
        response.raise_for_status()
        content = response.json()["message"]["content"]
        return _parse_response(content, candidates, config)

    except httpx.ConnectError:
        raise RuntimeError(
            f"Cannot connect to Ollama at {base_url}.\n"
            "Start it with: ollama serve"
        )
    except RuntimeError:
        raise
    except Exception as e:
        logger.error("Ollama ranking failed: %s", e)
        raise RuntimeError(f"Ollama error: {e}") from e


# ── Helpers ────────────────────────────────────────────────────────────────────

def _top_candidates(flagged: list[FlaggedListing], n: int) -> list[FlaggedListing]:
    """
    Pre-sort by cap rate descending and return the top N candidates.

    HIGH-risk properties are pushed to the back so the model sees the best
    low-risk deals first. Listings with no financial data go last.
    """
    def sort_key(f: FlaggedListing) -> tuple:
        risk_penalty = 1 if f.risks.overall_risk.value == "HIGH" else 0
        cap_rate = f.financials.cap_rate or -999.0
        return (risk_penalty, -cap_rate)

    return sorted(flagged, key=sort_key)[:n]


def _build_listings_data(candidates: list[FlaggedListing]) -> list[dict]:
    return [
        {
            "address": f.listing.address,
            "price": f.listing.price,
            "beds": f.listing.beds,
            "baths": f.listing.baths,
            "sqft": f.listing.sqft,
            "lot_sqft": f.listing.lot_sqft,
            "home_type": f.listing.home_type,
            "days_on_market": f.listing.days_on_market,
            "estimated_monthly_rent": f.estimated_monthly_rent,
            "cap_rate_pct": f"{f.financials.cap_rate:.2%}" if f.financials.cap_rate else None,
            "coc_return_pct": f"{f.financials.coc_return:.2%}" if f.financials.coc_return else None,
            "monthly_cashflow": round(f.financials.monthly_cashflow) if f.financials.monthly_cashflow else None,
            "flood_zone": f.risks.flood_zone,
            "risk_level": f.risks.overall_risk.value,
            "zoning": f.listing.zoning,
            "tax_assessed_value": f.listing.tax_assessed_value,
            "zoning_potential_score": f.zoning_potential.development_score if f.zoning_potential else None,
            "zoning_potential_summary": f.zoning_potential.summary if f.zoning_potential else None,
        }
        for f in candidates
    ]


def _build_prompt(listings_data: list[dict], config: InvestmentConfig) -> str:
    c = config.criteria
    a = config.financial_assumptions
    n = config.output.max_shortlist

    # Numbered list of addresses for easy reference
    address_list = "\n".join(
        f"{i+1}. {d['address']}" for i, d in enumerate(listings_data)
    )

    return f"""You are a real estate investment analyst. From these {len(listings_data)} pre-screened properties, pick the top {n} investment deals.

Market: {config.output.market}
Target cap rate: {c.target_cap_rate:.1%} | Down payment: {a.down_payment_pct:.0%} | Loan rate: {a.loan_rate_annual:.2%}

CANDIDATE PROPERTIES (already sorted best-first by cap rate):
{json.dumps(listings_data, indent=2)}

ADDRESS LIST (use exact addresses from here):
{address_list}

RANKING RULES:
1. Rank by cap rate descending — higher cap rate = better deal
2. Prefer positive or least-negative monthly cash flow
3. Prefer LOW risk over HIGH risk
4. Penalize properties with negative cap rate
5. Use ONLY addresses from the address list above — do not invent or modify addresses

Return a JSON object:
{{
  "run_summary": "one sentence summarizing the top findings",
  "deals": [
    {{
      "rank": 1,
      "address": "exact address from the list above",
      "narrative": "2-3 sentences of qualitative context: neighborhood character, location strengths/weaknesses, development or zoning upside (use zoning_potential_summary if available), lot size signal, days-on-market story, and what type of buyer/investor this suits. Do NOT quote any numbers — no prices, cap rates, cash flow figures, or percentages."
    }}
  ]
}}

Include exactly {n} deals. Return only valid JSON."""


def _parse_response(
    content: str,
    candidates: list[FlaggedListing],
    config: InvestmentConfig,
) -> Shortlist:
    """
    Parse Ollama's response into a Shortlist.

    The model only provides ranking order and narratives — all financial data
    comes from the pipeline (candidates), never from the model's output.
    Invalid or hallucinated addresses are discarded; remaining slots are filled
    from the pre-sorted candidates.
    """
    # Build address lookup (case-insensitive, strip whitespace)
    candidates_by_address = {f.listing.address.strip().lower(): f for f in candidates}
    used_addresses: set[str] = set()

    try:
        data = json.loads(content)
        deals_raw = data.get("deals", [])
        run_summary = data.get(
            "run_summary",
            f"Top {config.output.max_shortlist} deals in {config.output.market}",
        )
    except json.JSONDecodeError as e:
        logger.error("Ollama returned invalid JSON: %s\nContent: %s", e, content[:500])
        raise RuntimeError(f"Ollama returned invalid JSON: {e}") from e

    deals: list[DealNarrative] = []

    # First pass: use addresses the model returned (validate each one)
    for d in deals_raw[: config.output.max_shortlist * 2]:  # allow some extras for fallback
        raw_address = (d.get("address") or "").strip()
        key = raw_address.lower()

        f = candidates_by_address.get(key)
        if f is None:
            logger.warning("Ollama returned unknown address %r — skipping", raw_address)
            continue
        if key in used_addresses:
            continue

        used_addresses.add(key)
        deals.append(_make_deal(len(deals) + 1, f, d.get("narrative", ""), config))

        if len(deals) >= config.output.max_shortlist:
            break

    # Second pass: fill remaining slots from pre-sorted candidates
    if len(deals) < config.output.max_shortlist:
        for f in candidates:
            key = f.listing.address.strip().lower()
            if key in used_addresses:
                continue
            used_addresses.add(key)
            deals.append(_make_deal(len(deals) + 1, f, "", config))
            if len(deals) >= config.output.max_shortlist:
                break

    return Shortlist(
        market=config.output.market,
        deals=deals,
        run_summary=run_summary,
    )


def _make_deal(
    rank: int,
    f: FlaggedListing,
    narrative: str,
    config: InvestmentConfig,
) -> DealNarrative:
    """Build a DealNarrative from real pipeline data + model-supplied narrative."""
    if not narrative:
        # Fallback narrative from raw numbers
        cap = f"{f.financials.cap_rate:.2%}" if f.financials.cap_rate else "N/A"
        cf = f"${f.financials.monthly_cashflow:+,.0f}/mo" if f.financials.monthly_cashflow else "N/A"
        narrative = (
            f"Cap rate {cap}, monthly cash flow {cf}. "
            f"Risk level: {f.risks.overall_risk.value}."
        )

    return DealNarrative(
        rank=rank,
        address=f.listing.address,
        price=f.listing.price or 0,
        beds=f.listing.beds,
        baths=f.listing.baths,
        sqft=f.listing.sqft,
        lot_sqft=f.listing.lot_sqft,
        home_type=f.listing.home_type,
        school_district=f.listing.school_district,
        hoa_fee=f.listing.hoa_fee,
        days_on_market=f.listing.days_on_market,
        cap_rate=f.financials.cap_rate,
        coc_return=f.financials.coc_return,
        monthly_cashflow=f.financials.monthly_cashflow,
        noi_annual=f.financials.noi_annual,
        monthly_mortgage=f.financials.monthly_mortgage,
        estimated_monthly_rent=f.estimated_monthly_rent,
        walk_score=f.walk_score,
        bike_score=f.bike_score,
        transit_score=f.transit_score,
        flood_zone=f.risks.flood_zone,
        tax_assessed_value=f.listing.tax_assessed_value,
        tax_assessed_land=f.listing.tax_assessed_land,
        tax_assessed_improvement=f.listing.tax_assessed_improvement,
        zoning=f.listing.zoning,
        risk_level=f.risks.overall_risk.value,
        narrative=narrative,
        zoning_potential=f.zoning_potential,
        appreciation=f.appreciation,
        latitude=f.listing.latitude,
        longitude=f.listing.longitude,
        listing_url=f.listing.listing_url,
        year_built=f.listing.year_built,
        nearby_schools=f.listing.nearby_schools,
        solar_ghi_annual=f.listing.solar_ghi_annual,
    )
