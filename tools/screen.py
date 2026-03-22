"""
Screen listings against investment criteria.

Pure functions — no I/O, no side effects.
"""
from tools.models import RawListing, ScreenResult, ScreeningCriteria


def screen_listing(listing: RawListing, criteria: ScreeningCriteria) -> ScreenResult:
    """
    Return ScreenResult(passed=True) if the listing meets all criteria,
    or ScreenResult(passed=False, reason=...) explaining why it was filtered.

    None fields are treated as disqualifying — we can't screen what we can't measure.
    """
    if listing.price is None:
        return ScreenResult(listing=listing, passed=False, reason="price_unknown")
    if listing.beds is None:
        return ScreenResult(listing=listing, passed=False, reason="beds_unknown")
    if listing.days_on_market is None:
        return ScreenResult(listing=listing, passed=False, reason="dom_unknown")

    if listing.price > criteria.max_price:
        return ScreenResult(listing=listing, passed=False, reason="price_too_high")
    if listing.beds < criteria.min_beds:
        return ScreenResult(listing=listing, passed=False, reason="beds_below_min")
    if listing.days_on_market > criteria.max_dom:
        return ScreenResult(listing=listing, passed=False, reason="dom_exceeded")

    return ScreenResult(listing=listing, passed=True)


def screen_all(
    listings: list[RawListing], criteria: ScreeningCriteria
) -> tuple[list[RawListing], list[dict]]:
    """
    Apply screen_listing to all listings.

    Returns:
        passed: listings that passed all criteria
        filtered_out: list of {"zpid", "address", "reason"} dicts for the run log
    """
    passed = []
    filtered_out = []

    for listing in listings:
        result = screen_listing(listing, criteria)
        if result.passed:
            passed.append(listing)
        else:
            filtered_out.append({
                "zpid": listing.zpid,
                "address": listing.address,
                "reason": result.reason,
            })

    return passed, filtered_out
