"""
Neighborhood enrichment: Walk Score + rent comps.

Async from day one so concurrent enrichment (asyncio.gather) can be
added without refactoring when real data is wired up (see TODOS.md).
"""
import asyncio
import logging
import os

import httpx

from tools.models import EnrichResult, RawListing

logger = logging.getLogger(__name__)

_WALKSCORE_API_KEY = os.getenv("WALKSCORE_API_KEY")
_WALKSCORE_URL = "https://api.walkscore.com/score"
_WALKSCORE_RETRIES = 2
_WALKSCORE_TIMEOUT = 5.0  # seconds


async def enrich_neighborhood(listing: RawListing) -> EnrichResult:
    """
    Enrich a listing with Walk Score and rent comps.

    Always returns EnrichResult (never raises).
    Fields are None if the data source is unavailable.
    """
    walk_score = await _fetch_walk_score(listing)

    # Rent comps: use value already on the listing (from fixture or Zillow zestimate).
    # A dedicated rent comps API will replace this when real data is integrated.
    estimated_monthly_rent = listing.estimated_monthly_rent

    return EnrichResult(
        listing=listing,
        walk_score=walk_score,
        estimated_monthly_rent=estimated_monthly_rent,
    )


async def enrich_all(listings: list[RawListing]) -> list[EnrichResult]:
    """
    Enrich all listings.

    Currently runs sequentially (fixture data is instant).
    When real API calls are added, swap to:
        return await asyncio.gather(*[enrich_neighborhood(l) for l in listings])
    """
    results = []
    for listing in listings:
        result = await enrich_neighborhood(listing)
        results.append(result)
    return results


async def _fetch_walk_score(listing: RawListing) -> int | None:
    """
    Fetch Walk Score for a listing address.

    Returns None (gracefully) if:
    - No API key configured
    - Walk Score already present on the listing (use it directly)
    - API returns error, rate-limits, or times out
    """
    # Use value from fixture/listing data if already present
    if listing.walk_score is not None:
        return listing.walk_score

    if not _WALKSCORE_API_KEY:
        return None

    if listing.latitude is None or listing.longitude is None:
        logger.warning("No coordinates for %s — skipping Walk Score", listing.zpid)
        return None

    params = {
        "format": "json",
        "address": listing.address,
        "lat": listing.latitude,
        "lon": listing.longitude,
        "wsapikey": _WALKSCORE_API_KEY,
    }

    for attempt in range(1, _WALKSCORE_RETRIES + 2):
        try:
            async with httpx.AsyncClient(timeout=_WALKSCORE_TIMEOUT) as client:
                response = await client.get(_WALKSCORE_URL, params=params)
                response.raise_for_status()
                data = response.json()
                return data.get("walkscore")

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 429:
                if attempt <= _WALKSCORE_RETRIES:
                    await asyncio.sleep(2 ** attempt)
                    continue
            logger.warning(
                "WalkScore API error for %s (attempt %d): %s",
                listing.zpid, attempt, e,
            )
            return None

        except (httpx.TimeoutException, httpx.RequestError) as e:
            logger.warning(
                "WalkScore request failed for %s (attempt %d): %s",
                listing.zpid, attempt, e,
            )
            return None

    return None
