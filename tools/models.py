"""
Pydantic models for all pipeline I/O.

Data flow:
  RawListing
    → screen_listing()   → ScreenResult
    → enrich()           → EnrichResult
    → analyze_financials() → AnalyzedListing
    → flag_risks()       → FlaggedListing
    → claude_rank_and_narrate() → Shortlist
"""
from enum import Enum
from pydantic import BaseModel, Field


# ─── Config ───────────────────────────────────────────────────────────────────

class ScreeningCriteria(BaseModel):
    max_price: float
    min_beds: int
    max_dom: int
    target_cap_rate: float = 0.05
    walkscore_min: int = 50
    dom_outlier_multiplier: float = 2.0
    max_hoa_fee: float | None = None  # monthly; None = no filter


class FinancialAssumptions(BaseModel):
    down_payment_pct: float = 0.25
    loan_rate_annual: float = 0.07
    loan_term_years: int = 30
    vacancy_rate: float = 0.08
    management_fee_pct: float = 0.10
    maintenance_pct_of_value: float = 0.01
    insurance_annual: float = 1200.0
    closing_cost_pct: float = 0.03
    property_tax_rate_pct: float = 0.012  # fallback if not in listing data


class OutputConfig(BaseModel):
    max_shortlist: int = 5
    market: str
    use_mock_ranker: bool = False


class InvestmentConfig(BaseModel):
    criteria: ScreeningCriteria
    financial_assumptions: FinancialAssumptions
    output: OutputConfig


# ─── Pipeline data ────────────────────────────────────────────────────────────

class RawListing(BaseModel):
    zpid: str
    address: str
    price: float | None = None
    beds: int | None = None
    baths: float | None = None
    sqft: int | None = None
    lot_sqft: int | None = None               # lot/land size in sqft (None for condos)
    home_type: str | None = None              # "Single Family", "Condo", "Townhouse", "Multi-Family"
    school_district: str | None = None        # school district / feeder zone
    hoa_fee: float | None = None              # monthly HOA fee; None = no HOA
    days_on_market: int | None = None
    zestimate: float | None = None
    estimated_monthly_rent: float | None = None
    property_tax_annual: float | None = None  # from listing; fallback: price × rate
    flood_zone: str | None = None             # from fixture or FEMA lookup
    walk_score: int | None = None             # from fixture or WalkScore API
    latitude: float | None = None
    longitude: float | None = None


class ScreenResult(BaseModel):
    listing: RawListing
    passed: bool
    reason: str | None = None  # populated when passed=False


class EnrichResult(BaseModel):
    listing: RawListing
    walk_score: int | None = None
    estimated_monthly_rent: float | None = None
    error: str | None = None
    # success is always True — partial enrichment (None fields) is acceptable


class FinancialResult(BaseModel):
    success: bool
    failure_reason: str | None = None
    cap_rate: float | None = None
    coc_return: float | None = None
    monthly_cashflow: float | None = None
    noi_annual: float | None = None
    monthly_mortgage: float | None = None
    total_cash_invested: float | None = None


class RiskLevel(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class RiskFlag(BaseModel):
    code: str
    description: str
    level: RiskLevel


class RiskResult(BaseModel):
    flood_zone: str | None = None
    flags: list[RiskFlag] = Field(default_factory=list)
    overall_risk: RiskLevel = RiskLevel.LOW


class AnalyzedListing(BaseModel):
    listing: RawListing
    walk_score: int | None = None
    estimated_monthly_rent: float | None = None
    financials: FinancialResult


class FlaggedListing(BaseModel):
    listing: RawListing
    walk_score: int | None = None
    estimated_monthly_rent: float | None = None
    financials: FinancialResult
    risks: RiskResult


# ─── Claude output ────────────────────────────────────────────────────────────

class DealNarrative(BaseModel):
    rank: int
    address: str
    price: float
    beds: int | None = None
    baths: float | None = None
    sqft: int | None = None
    lot_sqft: int | None = None
    home_type: str | None = None
    school_district: str | None = None
    hoa_fee: float | None = None
    days_on_market: int | None = None
    cap_rate: float | None = None
    coc_return: float | None = None
    monthly_cashflow: float | None = None
    walk_score: int | None = None
    flood_zone: str | None = None
    risk_level: str
    narrative: str


class Shortlist(BaseModel):
    market: str
    deals: list[DealNarrative]
    run_summary: str
