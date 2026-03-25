"""
Appreciation signal computation — pure rule-based, no I/O.

Signals derived from data already in the pipeline:
  - Price-to-rent ratio (Gross Rent Multiplier)
  - Assessment ratio (assessed value vs market price)
  - Land value percentage (how land-heavy the investment is)
  - Renovation candidate flag (pre-1975 year_built)

Score 1–5 calibrated for the Seattle/Greater Seattle market where
GRMs of 25–35 are typical. Properties that stand out are scored higher.
"""
from tools.models import AppreciationSignals, RawListing

_RENOVATION_YEAR_CUTOFF = 1975   # properties built before this year flagged as candidates
_SEATTLE_MEDIAN_GRM = 28.0       # rough Seattle market median; used for relative labeling


def compute_appreciation_signals(
    listing: RawListing,
    estimated_monthly_rent: float | None,
) -> AppreciationSignals:
    """
    Compute appreciation signals for a listing.

    All inputs come from the pipeline — no external calls.
    Returns AppreciationSignals with score 1–5 and a signals list.
    """
    signals: list[str] = []
    score = 1  # baseline

    price = listing.price
    if not price or price <= 0:
        return AppreciationSignals(appreciation_score=1, signals=["Price unavailable"])

    # ── Price-to-rent ratio (GRM) ─────────────────────────────────────────────
    price_to_rent = None
    if estimated_monthly_rent and estimated_monthly_rent > 0:
        price_to_rent = price / (estimated_monthly_rent * 12)
        if price_to_rent <= 15:
            signals.append(f"Excellent GRM {price_to_rent:.1f}× — strong yield signal")
            score += 2
        elif price_to_rent <= 22:
            signals.append(f"Good GRM {price_to_rent:.1f}× — above-average yield for Seattle area")
            score += 1
        elif price_to_rent <= _SEATTLE_MEDIAN_GRM:
            signals.append(f"GRM {price_to_rent:.1f}× — near Seattle median")
        else:
            signals.append(f"GRM {price_to_rent:.1f}× — below-average yield (typical for Seattle)")

    # ── Assessment ratio ──────────────────────────────────────────────────────
    assessment_ratio = None
    if listing.tax_assessed_value and listing.tax_assessed_value > 0:
        assessment_ratio = listing.tax_assessed_value / price
        if assessment_ratio < 0.65:
            signals.append(
                f"Assessed at {assessment_ratio:.0%} of price — "
                "significantly underassessed (lower current tax, potential future reassessment)"
            )
            score += 1
        elif assessment_ratio < 0.80:
            signals.append(f"Assessed at {assessment_ratio:.0%} of price — moderately underassessed")
        elif assessment_ratio > 1.10:
            signals.append(
                f"Assessed at {assessment_ratio:.0%} of price — "
                "assessed above market (verify market price accuracy)"
            )

    # ── Land value percentage ─────────────────────────────────────────────────
    land_value_pct = None
    if listing.tax_assessed_land and listing.tax_assessed_value and listing.tax_assessed_value > 0:
        land_value_pct = listing.tax_assessed_land / listing.tax_assessed_value
        if land_value_pct >= 0.70:
            signals.append(
                f"Land-heavy ({land_value_pct:.0%} of assessed is land) — "
                "appreciation driven by location; building value less critical"
            )
            score += 1
        elif land_value_pct >= 0.55:
            signals.append(f"Balanced land/building split ({land_value_pct:.0%} land)")

    # ── Renovation candidate ──────────────────────────────────────────────────
    renovation_candidate = False
    if listing.year_built and listing.year_built < _RENOVATION_YEAR_CUTOFF:
        renovation_candidate = True
        if assessment_ratio is not None and assessment_ratio < 0.75:
            signals.append(
                f"Pre-{_RENOVATION_YEAR_CUTOFF} construction ({listing.year_built}) + "
                "underassessed — renovation upside potential"
            )
            score += 1
        else:
            signals.append(
                f"Pre-{_RENOVATION_YEAR_CUTOFF} construction ({listing.year_built}) — "
                "renovation may be needed; value-add opportunity"
            )

    score = max(1, min(5, score))

    return AppreciationSignals(
        price_to_rent_ratio=round(price_to_rent, 2) if price_to_rent is not None else None,
        assessment_ratio=round(assessment_ratio, 3) if assessment_ratio is not None else None,
        land_value_pct=round(land_value_pct, 3) if land_value_pct is not None else None,
        renovation_candidate=renovation_candidate,
        appreciation_score=score,
        signals=signals,
    )
