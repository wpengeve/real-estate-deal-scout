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


def analyze_financials(
    enrich_result: EnrichResult,
    assumptions: FinancialAssumptions,
) -> AnalyzedListing:
    """
    Compute cap rate, cash-on-cash return, and monthly cash flow.

    Returns AnalyzedListing with financials.success=False if required
    data is missing (no rent estimate or zero price).
    """
    listing = enrich_result.listing
    monthly_rent = enrich_result.estimated_monthly_rent or listing.estimated_monthly_rent

    if monthly_rent is None:
        return AnalyzedListing(
            listing=listing,
            walk_score=enrich_result.walk_score,
            estimated_monthly_rent=None,
            financials=FinancialResult(
                success=False,
                failure_reason="no_rent_data",
            ),
        )

    price = listing.price
    if not price or price <= 0:
        return AnalyzedListing(
            listing=listing,
            walk_score=enrich_result.walk_score,
            estimated_monthly_rent=monthly_rent,
            financials=FinancialResult(
                success=False,
                failure_reason="invalid_price",
            ),
        )

    a = assumptions

    # Property taxes: use listing data if available, else estimate
    property_tax_annual = listing.property_tax_annual or (price * a.property_tax_rate_pct)

    # NOI
    annual_gross_rent = monthly_rent * 12
    annual_eff_rent = annual_gross_rent * (1 - a.vacancy_rate)
    management_annual = annual_eff_rent * a.management_fee_pct
    maintenance_annual = price * a.maintenance_pct_of_value
    noi = annual_eff_rent - management_annual - maintenance_annual - a.insurance_annual - property_tax_annual

    cap_rate = noi / price

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

    # Monthly cash flow
    monthly_cf = (
        monthly_rent * (1 - a.vacancy_rate)
        - monthly_mortgage
        - (management_annual / 12)
        - (maintenance_annual / 12)
        - (a.insurance_annual / 12)
        - (property_tax_annual / 12)
    )

    # Cash-on-cash return (negative cash flow is valid, not an error)
    coc = (monthly_cf * 12) / total_cash_invested

    return AnalyzedListing(
        listing=listing,
        walk_score=enrich_result.walk_score,
        estimated_monthly_rent=monthly_rent,
        financials=FinancialResult(
            success=True,
            cap_rate=cap_rate,
            coc_return=coc,
            monthly_cashflow=monthly_cf,
            noi_annual=noi,
            monthly_mortgage=monthly_mortgage,
            total_cash_invested=total_cash_invested,
        ),
    )


def analyze_all(
    enrich_results: list[EnrichResult],
    assumptions: FinancialAssumptions,
) -> list[AnalyzedListing]:
    return [analyze_financials(r, assumptions) for r in enrich_results]


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
