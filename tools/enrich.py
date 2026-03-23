"""
Neighborhood enrichment: Walk Score + HUD Fair Market Rents.

Walk Score:  requires WALKSCORE_API_KEY env var (free tier at walkscore.com)
HUD FMR:     requires HUD_API_KEY env var (free at huduser.gov/portal/dataset/fmr-api.html)
             Provides ZIP/county-level rent estimates by bedroom count.

Both degrade gracefully — missing keys or API failures return None fields,
not errors. The pipeline continues with whatever data is available.
"""
import asyncio
import logging
import os

import httpx

from tools.models import EnrichConfig, EnrichResult, RawListing

logger = logging.getLogger(__name__)

# ── Walk Score ─────────────────────────────────────────────────────────────────

_WALKSCORE_API_KEY = os.getenv("WALKSCORE_API_KEY")
_WALKSCORE_URL = "https://api.walkscore.com/score"
_WALKSCORE_RETRIES = 2
_WALKSCORE_TIMEOUT = 5.0

# ── HUD Fair Market Rents ──────────────────────────────────────────────────────

_HUD_API_KEY = os.getenv("HUD_API_KEY")
_HUD_BASE_URL = "https://www.huduser.gov/hudapi/public/fmr"
_HUD_TIMEOUT = 10.0

# In-memory caches — populated once per process run
_hud_entity_id: str | None = None          # county entity ID (looked up once)
_hud_fmr_data: dict[str, int] | None = None  # bedroom → monthly rent

_HUD_BED_KEYS = {
    1: "One-Bedroom",
    2: "Two-Bedroom",
    3: "Three-Bedroom",
    4: "Four-Bedroom",
}


# ── Public API ─────────────────────────────────────────────────────────────────

async def enrich_neighborhood(
    listing: RawListing,
    enrich_config: EnrichConfig | None = None,
) -> EnrichResult:
    """
    Enrich a listing with Walk Score and rent estimate.

    Always returns EnrichResult (never raises).
    Fields are None when the data source is unavailable or unconfigured.
    """
    walk_score = await _fetch_walk_score(listing)

    # Use rent already on the listing if present; otherwise try HUD FMR
    rent = listing.estimated_monthly_rent
    if rent is None and enrich_config and _HUD_API_KEY and listing.beds:
        rent = await _fetch_hud_fmr_rent(listing.beds, enrich_config)

    return EnrichResult(
        listing=listing,
        walk_score=walk_score,
        estimated_monthly_rent=rent,
    )


async def enrich_all(
    listings: list[RawListing],
    enrich_config: EnrichConfig | None = None,
) -> list[EnrichResult]:
    """
    Enrich all listings sequentially.

    Swap to asyncio.gather when real API call volume warrants it (see TODOS.md).
    """
    results = []
    for listing in listings:
        result = await enrich_neighborhood(listing, enrich_config)
        results.append(result)
    return results


# ── Walk Score ─────────────────────────────────────────────────────────────────

async def _fetch_walk_score(listing: RawListing) -> int | None:
    """
    Fetch Walk Score for a listing.

    Returns None gracefully if no API key, no coordinates, or API fails.
    """
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
                return response.json().get("walkscore")

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 429 and attempt <= _WALKSCORE_RETRIES:
                await asyncio.sleep(2 ** attempt)
                continue
            logger.warning("WalkScore API error for %s (attempt %d): %s", listing.zpid, attempt, e)
            return None

        except (httpx.TimeoutException, httpx.RequestError) as e:
            logger.warning("WalkScore request failed for %s (attempt %d): %s", listing.zpid, attempt, e)
            return None

    return None


# ── HUD Fair Market Rents ──────────────────────────────────────────────────────

async def _fetch_hud_fmr_rent(beds: int, enrich_config: EnrichConfig) -> float | None:
    """
    Return the HUD Fair Market Rent (monthly) for the given bedroom count.

    Looks up the county entity ID once, then caches FMR data for the process lifetime.
    Returns None gracefully on any failure.
    """
    global _hud_entity_id, _hud_fmr_data

    # Resolve county entity ID (one API call per process run)
    if _hud_entity_id is None:
        _hud_entity_id = await _resolve_hud_entity_id(
            enrich_config.hud_state_fips,
            enrich_config.hud_county_name,
        )
        if _hud_entity_id is None:
            return None

    # Fetch FMR data for this county (one API call per process run)
    if _hud_fmr_data is None:
        _hud_fmr_data = await _fetch_hud_fmr_data(_hud_entity_id)
        if _hud_fmr_data is None:
            return None

    # Map bedroom count → HUD key (capped at 4BR)
    beds_clamped = min(max(beds, 1), 4)
    key = _HUD_BED_KEYS.get(beds_clamped)
    val = _hud_fmr_data.get(key) if key else None
    return float(val) if val else None


async def _resolve_hud_entity_id(state_fips: str, county_name: str) -> str | None:
    """
    Call HUD listCounties to find the entity ID for the target county.

    state_fips: e.g. "53" for Washington
    county_name: e.g. "King County" — matched as a case-insensitive substring
    """
    try:
        async with httpx.AsyncClient(timeout=_HUD_TIMEOUT) as client:
            resp = await client.get(
                f"{_HUD_BASE_URL}/listCounties/{state_fips}",
                headers={"Authorization": f"Bearer {_HUD_API_KEY}"},
            )
            resp.raise_for_status()
            counties = resp.json().get("data", [])

        target = county_name.lower()
        for county in counties:
            name = (county.get("area_name") or county.get("countyname") or "").lower()
            if target in name:
                entity_id = county.get("entityid")
                logger.info("HUD entity ID for %s: %s", county_name, entity_id)
                return entity_id

        logger.warning("HUD county '%s' not found in state FIPS %s", county_name, state_fips)
        return None

    except Exception as e:
        logger.warning("HUD county lookup failed: %s", e)
        return None


async def _fetch_hud_fmr_data(entity_id: str) -> dict[str, int] | None:
    """Fetch FMR bedroom rates for a county entity ID."""
    try:
        async with httpx.AsyncClient(timeout=_HUD_TIMEOUT) as client:
            resp = await client.get(
                f"{_HUD_BASE_URL}/data/{entity_id}",
                headers={"Authorization": f"Bearer {_HUD_API_KEY}"},
            )
            resp.raise_for_status()
            data = resp.json().get("data", {})
            logger.info(
                "HUD FMR loaded: 1BR=$%s 2BR=$%s 3BR=$%s",
                data.get("One-Bedroom"), data.get("Two-Bedroom"), data.get("Three-Bedroom"),
            )
            return data

    except Exception as e:
        logger.warning("HUD FMR data fetch failed for entity %s: %s", entity_id, e)
        return None
