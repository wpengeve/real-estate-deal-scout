"""
Risk flagging: flood zone classification and DOM outlier detection.

Flood zone data source:
  - If listing.flood_zone is set (from fixture or enrichment), use it directly.
  - Otherwise, attempt FEMA NFHL API lookup (requires lat/lon).
  - Gracefully degrades to "unknown" on any failure.

FEMA flood zone reference:
  High risk  (Zone A*): A, AE, AH, AO, AR, A99 — 1% annual flood chance
  High risk  (Zone V*): V, VE — coastal high velocity
  Moderate   (Zone B/X500): 0.2% annual flood chance
  Minimal    (Zone C/X):    outside 500-year floodplain
"""
import logging

import httpx

from tools.models import (
    AnalyzedListing,
    FlaggedListing,
    RiskFlag,
    RiskLevel,
    RiskResult,
    ScreeningCriteria,
)

logger = logging.getLogger(__name__)

_HIGH_RISK_ZONES = {"A", "AE", "AH", "AO", "AR", "A99", "V", "VE"}
_FEMA_URL = (
    "https://hazards.fema.gov/gis/nfhl/rest/services/public/NFHL/MapServer/28/query"
)
_FEMA_TIMEOUT = 8.0


def flag_risks(
    analyzed: AnalyzedListing,
    criteria: ScreeningCriteria,
    dom_baseline: float | None = None,
) -> FlaggedListing:
    """
    Evaluate flood zone and DOM outlier risk for an analyzed listing.

    Args:
        analyzed: output of analyze_financials
        criteria: screening config (dom_outlier_multiplier)
        dom_baseline: average DOM across all screened listings for outlier detection.
                      If None, DOM outlier check is skipped.

    Returns:
        FlaggedListing with populated RiskResult.
    """
    flags: list[RiskFlag] = []

    # ── Flood zone ────────────────────────────────────────────────────────────
    flood_zone = analyzed.listing.flood_zone
    if flood_zone is None:
        flood_zone = _lookup_fema_sync(analyzed.listing.latitude, analyzed.listing.longitude)

    if flood_zone is not None and flood_zone.upper() in _HIGH_RISK_ZONES:
        flags.append(RiskFlag(
            code="flood_zone_high",
            description=f"Property is in FEMA flood zone {flood_zone} (high risk)",
            level=RiskLevel.HIGH,
        ))

    # ── DOM outlier ───────────────────────────────────────────────────────────
    dom = analyzed.listing.days_on_market
    if dom is not None and dom_baseline is not None and dom_baseline > 0:
        threshold = dom_baseline * criteria.dom_outlier_multiplier
        # DOM=0 means just listed — explicitly not a risk flag
        if dom > 0 and dom > threshold:
            flags.append(RiskFlag(
                code="high_dom",
                description=(
                    f"Property has been on market {dom} days "
                    f"(market average: {dom_baseline:.0f} days, "
                    f"threshold: {threshold:.0f} days)"
                ),
                level=RiskLevel.MEDIUM,
            ))

    # ── Overall risk level ────────────────────────────────────────────────────
    if any(f.level == RiskLevel.HIGH for f in flags):
        overall = RiskLevel.HIGH
    elif any(f.level == RiskLevel.MEDIUM for f in flags):
        overall = RiskLevel.MEDIUM
    else:
        overall = RiskLevel.LOW

    return FlaggedListing(
        listing=analyzed.listing,
        walk_score=analyzed.walk_score,
        estimated_monthly_rent=analyzed.estimated_monthly_rent,
        financials=analyzed.financials,
        risks=RiskResult(
            flood_zone=flood_zone or "unknown",
            flags=flags,
            overall_risk=overall,
        ),
    )


def flag_all(
    analyzed_listings: list[AnalyzedListing],
    criteria: ScreeningCriteria,
) -> list[FlaggedListing]:
    """
    Flag risks for all analyzed listings.
    Computes DOM baseline from the listings themselves.
    """
    dom_values = [
        a.listing.days_on_market
        for a in analyzed_listings
        if a.listing.days_on_market is not None
    ]
    dom_baseline = sum(dom_values) / len(dom_values) if dom_values else None

    return [flag_risks(a, criteria, dom_baseline) for a in analyzed_listings]


def _lookup_fema_sync(lat: float | None, lon: float | None) -> str | None:
    """
    Synchronous FEMA NFHL flood zone lookup.

    Returns flood zone string (e.g. "X", "AE") or None on failure.
    This is a sync call — fine for MVP. Make async when concurrency matters.
    """
    if lat is None or lon is None:
        return None

    params = {
        "geometry": f"{lon},{lat}",
        "geometryType": "esriGeometryPoint",
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": "FLD_ZONE",
        "f": "json",
    }

    try:
        with httpx.Client(timeout=_FEMA_TIMEOUT) as client:
            response = client.get(_FEMA_URL, params=params)
            response.raise_for_status()
            data = response.json()
            features = data.get("features", [])
            if features:
                return features[0].get("attributes", {}).get("FLD_ZONE")
    except (httpx.TimeoutException, httpx.RequestError, httpx.HTTPStatusError) as e:
        logger.warning("FEMA lookup failed for (%s, %s): %s", lat, lon, e)

    return None
