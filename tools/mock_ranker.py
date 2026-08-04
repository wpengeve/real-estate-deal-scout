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


def _score_rental(f: FlaggedListing) -> float:
    """Rental: weight cap rate + CoC, subtract risk penalty."""
    if not f.financials.success:
        return -999.0
    cap = f.financials.cap_rate or 0.0
    coc = f.financials.coc_return or 0.0
    penalty = _RISK_PENALTY.get(f.risks.overall_risk, 0.0)
    return cap * 0.6 + coc * 0.4 + penalty


def _score_primary(f: FlaggedListing, max_price: float) -> float:
    """Primary: prefer lower PITI and better walkability, penalise risk."""
    if not f.financials.success:
        return -999.0
    # Normalise PITI: lower is better (invert and scale 0–1)
    piti = f.financials.monthly_piti or 0.0
    piti_score = 1.0 - min(piti / 10_000, 1.0)
    walk = (f.walk_score or 0) / 100.0
    penalty = _RISK_PENALTY.get(f.risks.overall_risk, 0.0)
    return piti_score * 0.5 + walk * 0.5 + penalty


def _verdict_rental(f: FlaggedListing, config: InvestmentConfig) -> str:
    fin = f.financials
    target = config.criteria.target_cap_rate
    cap = fin.cap_rate or 0.0
    cf = fin.monthly_cashflow or 0.0

    if not fin.success:
        return "Pass — no financial data available."
    if cap >= target and cf >= 0:
        return "Strong Buy — cap rate meets target with positive cash flow."
    if cap >= target * 0.8 and cf >= -200:
        return "Buy — solid fundamentals, manageable cash flow gap."
    if cap >= target * 0.5 and f.risks.overall_risk != RiskLevel.HIGH:
        return "Consider — below target but location or appreciation potential may justify."
    if cap >= target * 0.2:
        return "Proceed with Caution — significant gap; needs price cut or higher rent."
    return "Pass — deeply negative returns with no clear path to profitability."


def _verdict_primary(f: FlaggedListing) -> str:
    ws = f.walk_score or 0
    risk = f.risks.overall_risk

    if not f.financials.success:
        return "Pass — no financial data available."
    if risk == RiskLevel.HIGH:
        return "Proceed with Caution — high risk flag; review before purchasing."
    if ws >= 70:
        return "Strong Buy — excellent walkability and solid location."
    if ws >= 50:
        return "Buy — good walkability with solid neighborhood fundamentals."
    if risk == RiskLevel.MEDIUM:
        return "Consider — car-dependent area with some risk factors to review."
    return "Consider — car-dependent area; verify commute and lifestyle fit."


def _narrative(rank: int, f: FlaggedListing, config: InvestmentConfig) -> str:
    fin = f.financials
    risk = f.risks

    if not fin.success:
        return (
            f"Financial analysis unavailable (reason: {fin.failure_reason}).\n"
            f"Insufficient data to assess merit.\n"
            "Pass — no financial data available."
        )

    line1 = f"{f.listing.home_type or 'Property'} at {f.listing.address.split(',')[0]}."

    if config.purpose == "primary":
        piti_str = f"${fin.monthly_piti:,.0f}/mo" if fin.monthly_piti else "—"
        hoa_note = f" + ${f.listing.hoa_fee:,.0f}/mo HOA" if f.listing.hoa_fee else ""
        line2 = f"Monthly PITI {piti_str}{hoa_note}."
        if risk.overall_risk == RiskLevel.HIGH:
            flag_descs = "; ".join(rf.description for rf in risk.flags[:2])
            line2 += f" HIGH RISK: {flag_descs}."
        line3 = _verdict_primary(f)
    else:
        cap_str = f"{fin.cap_rate:.1%}" if fin.cap_rate is not None else "N/A"
        cf_str = f"${fin.monthly_cashflow:+,.0f}/mo" if fin.monthly_cashflow is not None else "N/A"
        rent_str = (
            f"${f.estimated_monthly_rent:,.0f}/mo"
            if f.estimated_monthly_rent is not None
            else "unknown rent"
        )
        line2 = f"Cap rate {cap_str}, cash flow {cf_str} on {rent_str} estimated rent."
        if risk.overall_risk == RiskLevel.HIGH:
            flag_descs = "; ".join(rf.description for rf in risk.flags[:2])
            line2 += f" HIGH RISK: {flag_descs}."
        line3 = _verdict_rental(f, config)

    return f"{line1}\n{line2}\n{line3}"


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

    if config.purpose == "primary":
        max_price = config.criteria.max_price
        ranked = sorted(flagged, key=lambda f: _score_primary(f, max_price), reverse=True)[: config.output.max_shortlist]
    else:
        ranked = sorted(flagged, key=_score_rental, reverse=True)[: config.output.max_shortlist]

    deals = []
    for rank, f in enumerate(ranked, start=1):
        deals.append(
            DealNarrative(
                rank=rank,
                address=f.listing.address,
                price=f.listing.price or 0.0,
                beds=f.listing.beds,
                baths=f.listing.baths,
                sqft=f.listing.sqft,
                lot_sqft=f.listing.lot_sqft,
                home_type=f.listing.home_type,
                school_district=f.listing.school_district,
                hoa_fee=f.listing.hoa_fee,
                days_on_market=f.listing.days_on_market,
                market_context=f.market_context,
                cap_rate=f.financials.cap_rate,
                coc_return=f.financials.coc_return,
                monthly_cashflow=f.financials.monthly_cashflow,
                noi_annual=f.financials.noi_annual,
                monthly_mortgage=f.financials.monthly_mortgage,
                monthly_piti=f.financials.monthly_piti,
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
                risk_flags=[rf.description for rf in f.risks.flags],
                narrative=_narrative(rank, f, config),
                zoning_potential=f.zoning_potential,
                appreciation=f.appreciation,
                latitude=f.listing.latitude,
                longitude=f.listing.longitude,
                listing_url=f.listing.listing_url,
                year_built=f.listing.year_built,
                nearby_schools=f.listing.nearby_schools,
                solar_ghi_annual=f.listing.solar_ghi_annual,
                has_primary_suite=f.has_primary_suite,
                has_garage=f.has_garage,
                garage_spaces=f.garage_spaces,
                has_basement=f.has_basement,
                basement_finished=f.basement_finished,
                has_fireplace=f.has_fireplace,
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
        purpose=config.purpose,
    )
