"""
Financial analysis for real estate investment properties.

All calculations are pure functions — no I/O, no side effects.

Formulas
--------
Monthly mortgage (standard amortization):
    P  = loan_amount
    r  = loan_rate_annual / 12     (monthly rate)
    n  = loan_term_years * 12      (total payments)
    payment = P * r * (1+r)^n / ((1+r)^n - 1)

NOI (Net Operating Income, annual):
    gross_rent  = monthly_rent * 12
    eff_rent    = gross_rent * (1 - vacancy_rate)
    NOI = eff_rent
          - management_fee (eff_rent * management_fee_pct)
          - maintenance    (price * maintenance_pct_of_value)
          - insurance      (insurance_annual from config)
          - property_taxes (listing.property_tax_annual or price * property_tax_rate_pct)

Cap Rate:
    cap_rate = NOI / price

Monthly cash flow:
    monthly_cf = (monthly_rent * (1 - vacancy_rate))
                 - monthly_mortgage
                 - management_fee_monthly
                 - maintenance_monthly
                 - insurance_monthly
                 - property_taxes_monthly

Cash-on-Cash Return:
    total_cash_invested = down_payment + closing_costs
    coc = (monthly_cf * 12) / total_cash_invested
"""
from tools.models import AnalyzedListing, EnrichResult, FinancialAssumptions, FinancialResult

_FEATURE_FIELDS = (
    "has_primary_suite", "has_garage", "garage_spaces", "has_basement",
    "basement_finished", "has_fireplace", "site_features", "lot_features",
    "listing_remarks",
)


def _features(e: EnrichResult) -> dict:
    return {f: getattr(e, f) for f in _FEATURE_FIELDS}


def analyze_financials(
    enrich_result: EnrichResult,
    assumptions: FinancialAssumptions,
    purpose: str = "rental",
) -> AnalyzedListing:
    """
    Compute financial metrics.

    For rental: cap rate, cash-on-cash return, monthly cash flow, and PITI.
    For primary: PITI only (no rental income assumed).

    Returns AnalyzedListing with financials.success=False if price is missing.
    For rental, also fails when rent data is unavailable.
    """
    listing = enrich_result.listing
    monthly_rent = enrich_result.estimated_monthly_rent or listing.estimated_monthly_rent

    if purpose != "primary" and monthly_rent is None:
        return AnalyzedListing(
            listing=listing,
            walk_score=enrich_result.walk_score,
            bike_score=enrich_result.bike_score,
            transit_score=enrich_result.transit_score,
            estimated_monthly_rent=None,
            financials=FinancialResult(success=False, failure_reason="no_rent_data"),
            **_features(enrich_result),
        )

    price = listing.price
    if not price or price <= 0:
        return AnalyzedListing(
            listing=listing,
            walk_score=enrich_result.walk_score,
            bike_score=enrich_result.bike_score,
            transit_score=enrich_result.transit_score,
            estimated_monthly_rent=monthly_rent,
            financials=FinancialResult(success=False, failure_reason="invalid_price"),
            **_features(enrich_result),
        )

    a = assumptions

    # Property taxes: use listing data if available, else estimate
    property_tax_annual = listing.property_tax_annual or (price * a.property_tax_rate_pct)

    # Insurance: 0.3% of value floor for high-value properties
    insurance_annual = max(a.insurance_annual, price * 0.003)

    # Financing
    loan_amount = price * (1 - a.down_payment_pct)
    down_payment = price * a.down_payment_pct
    closing_costs = price * a.closing_cost_pct
    total_cash_invested = down_payment + closing_costs
    monthly_mortgage = _amortization_payment(
        principal=loan_amount,
        annual_rate=a.loan_rate_annual,
        term_years=a.loan_term_years,
    )

    # PITI = principal + interest + taxes + insurance (no vacancy/mgmt for owner-occupied)
    monthly_piti = monthly_mortgage + property_tax_annual / 12 + insurance_annual / 12

    if purpose == "primary":
        # No rental income — return PITI-only result
        return AnalyzedListing(
            listing=listing,
            walk_score=enrich_result.walk_score,
            bike_score=enrich_result.bike_score,
            transit_score=enrich_result.transit_score,
            estimated_monthly_rent=None,
            financials=FinancialResult(
                success=True,
                monthly_mortgage=monthly_mortgage,
                monthly_piti=monthly_piti,
                total_cash_invested=total_cash_invested,
            ),
            **_features(enrich_result),
        )

    # Rental: full NOI / cap rate / cash flow analysis
    annual_gross_rent = monthly_rent * 12  # type: ignore[operator]
    annual_eff_rent = annual_gross_rent * (1 - a.vacancy_rate)
    management_annual = annual_eff_rent * a.management_fee_pct
    maintenance_annual = price * a.maintenance_pct_of_value
    noi = annual_eff_rent - management_annual - maintenance_annual - insurance_annual - property_tax_annual

    cap_rate = noi / price

    monthly_cf = (
        monthly_rent * (1 - a.vacancy_rate)  # type: ignore[operator]
        - monthly_mortgage
        - (management_annual / 12)
        - (maintenance_annual / 12)
        - (insurance_annual / 12)
        - (property_tax_annual / 12)
    )

    coc = (monthly_cf * 12) / total_cash_invested

    return AnalyzedListing(
        listing=listing,
        walk_score=enrich_result.walk_score,
        bike_score=enrich_result.bike_score,
        transit_score=enrich_result.transit_score,
        estimated_monthly_rent=monthly_rent,
        financials=FinancialResult(
            success=True,
            cap_rate=cap_rate,
            coc_return=coc,
            monthly_cashflow=monthly_cf,
            noi_annual=noi,
            monthly_mortgage=monthly_mortgage,
            monthly_piti=monthly_piti,
            total_cash_invested=total_cash_invested,
        ),
        **_features(enrich_result),
    )


def analyze_all(
    enrich_results: list[EnrichResult],
    assumptions: FinancialAssumptions,
    purpose: str = "rental",
) -> list[AnalyzedListing]:
    return [analyze_financials(r, assumptions, purpose) for r in enrich_results]


def _amortization_payment(principal: float, annual_rate: float, term_years: int) -> float:
    """
    Standard fixed-rate mortgage payment formula:
        M = P * [r(1+r)^n] / [(1+r)^n - 1]

    where r = monthly rate, n = total payment count.
    """
    monthly_rate = annual_rate / 12
    n = term_years * 12
    if monthly_rate == 0:
        return principal / n
    factor = (1 + monthly_rate) ** n
    return principal * (monthly_rate * factor) / (factor - 1)
