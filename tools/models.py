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

class FetchConfig(BaseModel):
    data_source: str = "fixtures"      # "fixtures", "csv", "redfin", or "scraperapi"
    csv_path: str = "data/redfin.csv"  # single CSV path (used when csv_paths is empty)
    csv_paths: list[str] = Field(default_factory=list)  # multiple CSVs — merged and deduplicated by address
    redfin_region_id: int = 118        # King County, WA — find yours: go to redfin.com,
    redfin_region_type: int = 5        # search your area, click Download All, check the URL
    redfin_max_homes: int = 350        # Redfin's hard cap per request
    scraperapi_search_urls: list[str] = Field(default_factory=list)  # Redfin search URLs to scrape


class EnrichConfig(BaseModel):
    hud_state_fips: str | None = None       # auto-detected from listing ZIP via crosswalk; override e.g. "53" for WA
    hud_county_name: str | None = None      # auto-detected from listing ZIP via crosswalk; override e.g. "King County"
    hud_rent_multiplier: float = 1.0        # scale HUD FMR rent up for high-cost markets (e.g. 1.25 for Seattle)


class ScreeningCriteria(BaseModel):
    max_price: float
    min_beds: int
    min_baths: float | None = None            # e.g. 2.0; None = no filter
    max_dom: int
    target_cap_rate: float = 0.05
    walkscore_min: int = 50
    dom_outlier_multiplier: float = 2.0
    max_hoa_fee: float | None = None          # monthly; None = no filter
    min_cap_rate: float | None = None         # e.g. 0.04 = 4%; None = no filter
    preferred_home_types: list[str] | None = None  # None = no filter; e.g. ["Single Family"]
    allowed_cities: list[str] | None = None   # None = no filter; e.g. ["Seattle", "Bellevue"]
    require_primary_suite: bool = False       # filter listings without a primary bedroom


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
    ranker: str = "mock"               # "mock" | "ollama" | "claude"
    ollama_model: str = "llama3.2"     # model name as shown in `ollama list`
    ollama_base_url: str = "http://localhost:11434"
    use_mock_ranker: bool = True       # legacy alias — overridden by ranker field


class InvestmentConfig(BaseModel):
    fetch: FetchConfig = Field(default_factory=FetchConfig)
    enrich: EnrichConfig = Field(default_factory=EnrichConfig)
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
    tax_assessed_value: float | None = None       # land + improvement (total)
    tax_assessed_land: float | None = None        # land-only assessed value
    tax_assessed_improvement: float | None = None # building/structure assessed value
    zoning: str | None = None                 # zoning code e.g. "SF 5000", "LR1", "MR"
    flood_zone: str | None = None             # from fixture or FEMA lookup
    walk_score: int | None = None             # from fixture or WalkScore API
    latitude: float | None = None
    longitude: float | None = None
    listing_url: str | None = None            # full Redfin listing URL
    year_built: int | None = None
    nearby_schools: list["SchoolInfo"] | None = None  # populated during enrichment
    solar_ghi_annual: float | None = None     # avg GHI kWh/m²/day (NREL); higher = more sun


class ScreenResult(BaseModel):
    listing: RawListing
    passed: bool
    reason: str | None = None  # populated when passed=False


class EnrichResult(BaseModel):
    listing: RawListing
    walk_score: int | None = None
    bike_score: int | None = None
    transit_score: int | None = None
    estimated_monthly_rent: float | None = None
    error: str | None = None
    # success is always True — partial enrichment (None fields) is acceptable
    # ── Redfin listing page features ──────────────────────────────────────────
    has_primary_suite: bool | None = None
    has_garage: bool | None = None
    garage_spaces: int | None = None
    has_basement: bool | None = None
    basement_finished: bool | None = None
    has_fireplace: bool | None = None
    site_features: list[str] = []
    lot_features: list[str] = []
    listing_remarks: str | None = None


class FinancialResult(BaseModel):
    success: bool
    failure_reason: str | None = None
    cap_rate: float | None = None
    coc_return: float | None = None
    monthly_cashflow: float | None = None
    noi_annual: float | None = None
    monthly_mortgage: float | None = None
    total_cash_invested: float | None = None


class SchoolInfo(BaseModel):
    nces_id: str
    name: str
    level: str                          # "Elementary", "Middle", "High", "School"
    distance_miles: float | None = None
    proficiency_score: float | None = None  # avg math+reading % proficient (0–100)


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


class AppreciationSignals(BaseModel):
    price_to_rent_ratio: float | None = None    # price / annual_rent (GRM); lower = better yield
    assessment_ratio: float | None = None        # assessed_value / price; <0.8 = underassessed
    land_value_pct: float | None = None          # land_value / total_assessed; high = land-driven
    renovation_candidate: bool = False           # year_built < 1975 (value-add potential)
    appreciation_score: int | None = None        # 1–5 composite signal score
    signals: list[str] = Field(default_factory=list)  # human-readable signal descriptions


class ZoningPotential(BaseModel):
    zoned_units: int | None = None            # max units current zoning allows
    adu_eligible: bool = False                # can add attached ADU (in-law suite)
    dadu_eligible: bool = False               # can add detached ADU (backyard cottage)
    subdivision_eligible: bool = False        # lot large enough to subdivide
    hb1110_duplex: bool = False               # WA HB 1110 duplex rights apply
    development_score: int | None = None      # 1–5 (5 = strongest potential)
    opportunities: list[str] = Field(default_factory=list)
    summary: str | None = None


class _ListingFeaturesMixin(BaseModel):
    """Redfin-scraped features carried through the pipeline."""
    has_primary_suite: bool | None = None
    has_garage: bool | None = None
    garage_spaces: int | None = None
    has_basement: bool | None = None
    basement_finished: bool | None = None
    has_fireplace: bool | None = None
    site_features: list[str] = []
    lot_features: list[str] = []
    listing_remarks: str | None = None


class AnalyzedListing(_ListingFeaturesMixin):
    listing: RawListing
    walk_score: int | None = None
    bike_score: int | None = None
    transit_score: int | None = None
    estimated_monthly_rent: float | None = None
    financials: FinancialResult


class FlaggedListing(_ListingFeaturesMixin):
    listing: RawListing
    walk_score: int | None = None
    bike_score: int | None = None
    transit_score: int | None = None
    estimated_monthly_rent: float | None = None
    financials: FinancialResult
    risks: RiskResult
    zoning_potential: ZoningPotential | None = None
    appreciation: AppreciationSignals | None = None


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
    noi_annual: float | None = None            # pre-financing NOI — used for JS recalculation
    monthly_mortgage: float | None = None      # baseline mortgage — used for JS recalculation
    estimated_monthly_rent: float | None = None  # used for JS recalculation
    walk_score: int | None = None
    bike_score: int | None = None
    transit_score: int | None = None
    flood_zone: str | None = None
    tax_assessed_value: float | None = None
    tax_assessed_land: float | None = None
    tax_assessed_improvement: float | None = None
    zoning: str | None = None
    risk_level: str
    narrative: str
    zoning_potential: ZoningPotential | None = None
    appreciation: AppreciationSignals | None = None
    latitude: float | None = None
    longitude: float | None = None
    listing_url: str | None = None
    year_built: int | None = None
    nearby_schools: list[SchoolInfo] | None = None
    solar_ghi_annual: float | None = None
    # Redfin-scraped property features (populated when data_source=scraperapi)
    has_primary_suite: bool | None = None
    has_garage: bool | None = None
    garage_spaces: int | None = None
    has_basement: bool | None = None
    basement_finished: bool | None = None
    has_fireplace: bool | None = None


class Shortlist(BaseModel):
    market: str
    deals: list[DealNarrative]
    run_summary: str = ""
