"""
Mock ranker: heuristic-based shortlist with no API call.

Used when output.use_mock_ranker: true is set in config.yaml.
Accepts the same signature as _claude_rank_and_narrate() and returns
a Shortlist built from real financial and risk data on each listing.
"""
from rich.console import Console

from tools.models import DealNarrative, FlaggedListing, InvestmentConfig, RiskLevel, Shortlist

console = Console()

_RISK_PENALTY = {RiskLevel.HIGH: -0.05, RiskLevel.MEDIUM: -0.02, RiskLevel.LOW: 0.0}


def _score(f: FlaggedListing) -> float:
    """
    Simple ranking heuristic so the mock produces a sensible order.
    Higher is better. Weights cap rate most heavily, adds CoC, subtracts
    a risk penalty, and deprioritises listings with no financial data.
    """
    if not f.financials.success:
        return -999.0
    cap = f.financials.cap_rate or 0.0
    coc = f.financials.coc_return or 0.0
    penalty = _RISK_PENALTY.get(f.risks.overall_risk, 0.0)
    return cap * 0.6 + coc * 0.4 + penalty


def _narrative(rank: int, f: FlaggedListing, config: InvestmentConfig) -> str:
    fin = f.financials
    risk = f.risks

    if not fin.success:
        return (
            f"Financial analysis unavailable for this property "
            f"(reason: {fin.failure_reason}). "
            "Insufficient data to assess investment merit — ranked last."
        )

    cap_str = f"{fin.cap_rate:.1%}" if fin.cap_rate is not None else "N/A"
    coc_str = f"{fin.coc_return:.1%}" if fin.coc_return is not None else "N/A"
    cf_str = (
        f"${fin.monthly_cashflow:+,.0f}/mo" if fin.monthly_cashflow is not None else "N/A"
    )
    rent_str = (
        f"${f.estimated_monthly_rent:,.0f}/mo"
        if f.estimated_monthly_rent is not None
        else "unknown rent"
    )

    parts = [
        f"Cap rate {cap_str}, CoC {coc_str}, monthly cash flow {cf_str} "
        f"on {rent_str} estimated rent."
    ]

    if risk.overall_risk == RiskLevel.HIGH:
        flag_descs = ", ".join(rf.description for rf in risk.flags)
        parts.append(f"HIGH RISK: {flag_descs}.")
    elif risk.overall_risk == RiskLevel.MEDIUM:
        flag_descs = ", ".join(rf.description for rf in risk.flags)
        parts.append(f"Medium risk noted: {flag_descs}.")

    if fin.cap_rate is not None and fin.cap_rate >= config.criteria.target_cap_rate:
        parts.append(
            f"Cap rate exceeds target of {config.criteria.target_cap_rate:.1%} — "
            "meets investment threshold."
        )
    elif fin.cap_rate is not None:
        parts.append(
            f"Cap rate below target of {config.criteria.target_cap_rate:.1%} — "
            "verify rent assumptions."
        )

    return " ".join(parts)


def mock_rank_and_narrate(
    flagged: list[FlaggedListing],
    config: InvestmentConfig,
) -> Shortlist:
    """
    Heuristic shortlist — no API call.

    Sorts by a weighted score (cap rate + CoC - risk penalty), takes the
    top max_shortlist, and builds DealNarrative objects from real data.
    """
    console.print(
        "\n[bold yellow]! MOCK MODE - no API call made[/bold yellow]\n"
        "  (set [bold]use_mock_ranker: false[/bold] in config.yaml to use Claude)\n"
    )

    ranked = sorted(flagged, key=_score, reverse=True)[: config.output.max_shortlist]

    deals = []
    for rank, f in enumerate(ranked, start=1):
        deals.append(
            DealNarrative(
                rank=rank,
                address=f.listing.address,
                price=f.listing.price or 0.0,
                beds=f.listing.beds,
                days_on_market=f.listing.days_on_market,
                cap_rate=f.financials.cap_rate,
                coc_return=f.financials.coc_return,
                monthly_cashflow=f.financials.monthly_cashflow,
                walk_score=f.walk_score,
                flood_zone=f.risks.flood_zone,
                risk_level=f.risks.overall_risk.value,
                narrative=_narrative(rank, f, config),
            )
        )

    n_total = len(flagged)
    n_shown = len(deals)
    return Shortlist(
        market=config.output.market,
        deals=deals,
        run_summary=(
            f"{n_shown} of {n_total} screened listings ranked "
            f"(mock mode — heuristic scoring, no Claude call)."
        ),
    )
