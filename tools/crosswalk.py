"""
USPS ZIP Crosswalk — auto-detect county from listing ZIP code.

Chain:
  ZIP code
    → HUD USPS crosswalk API (type=2, ZIP→county FIPS)
    → Census Bureau API (FIPS→county name)
    → (state_fips, county_name)  e.g. ("53", "King County")

Both APIs are free with no key required for the Census Bureau.
HUD API requires HUD_API_KEY (same key used for HUD FMR).

Degrades gracefully — returns None on any failure.
Results are cached in-process so the same ZIP is only looked up once.
"""
import logging
import os
import re

import httpx

logger = logging.getLogger(__name__)

_HUD_API_KEY = os.getenv("HUD_API_KEY")
_HUD_USPS_URL = "https://www.huduser.gov/hudapi/public/usps"
_CENSUS_URL = "https://api.census.gov/data/2020/dec/pl"
_TIMEOUT = 10.0

# In-process cache: zip_code → (state_fips, county_name) or None
_cache: dict[str, tuple[str, str] | None] = {}


async def zip_to_county(zip_code: str) -> tuple[str, str] | None:
    """
    Resolve a ZIP code to (state_fips, county_name).

    Returns e.g. ("53", "King County") for ZIP 98118.
    Returns None if HUD_API_KEY is missing, ZIP is unknown, or any API fails.
    """
    if zip_code in _cache:
        return _cache[zip_code]

    result = await _resolve(zip_code)
    _cache[zip_code] = result
    return result


async def _resolve(zip_code: str) -> tuple[str, str] | None:
    if not _HUD_API_KEY:
        logger.debug("HUD_API_KEY not set — ZIP crosswalk unavailable")
        return None

    geoid = await _hud_zip_to_geoid(zip_code)
    if not geoid or len(geoid) < 5:
        return None

    # geoid is a 5-digit county FIPS: first 2 = state, last 3 = county
    state_fips = geoid[:2]
    county_fips = geoid[2:]

    county_name = await _census_county_name(state_fips, county_fips)
    if not county_name:
        return None

    # Strip state suffix: "King County, Washington" → "King County"
    short_name = re.sub(r",\s*.+$", "", county_name).strip()
    logger.info("ZIP %s → %s (state FIPS %s)", zip_code, short_name, state_fips)
    return state_fips, short_name


async def _hud_zip_to_geoid(zip_code: str) -> str | None:
    """Call HUD USPS crosswalk (type=2 = ZIP→county) and return the county geoid."""
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(
                _HUD_USPS_URL,
                params={"type": "2", "query": zip_code},
                headers={"Authorization": f"Bearer {_HUD_API_KEY}"},
            )
            resp.raise_for_status()
            data = resp.json()

        results = data.get("data", {}).get("results", [])
        if not results:
            logger.warning("HUD USPS crosswalk: no county found for ZIP %s", zip_code)
            return None

        # Take the first result; filter to res_ratio > 0 if multiple
        best = max(results, key=lambda r: r.get("res_ratio", 0))
        geoid = best.get("geoid")
        logger.debug("ZIP %s → geoid %s (res_ratio %.2f)", zip_code, geoid, best.get("res_ratio", 0))
        return geoid

    except Exception as e:
        logger.warning("HUD USPS crosswalk failed for ZIP %s: %s", zip_code, e)
        return None


async def _census_county_name(state_fips: str, county_fips: str) -> str | None:
    """Call Census Bureau API to get county name from state + county FIPS.

    Retries once on transient failures (Census API occasionally drops connections).
    """
    for attempt in range(2):
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.get(
                    _CENSUS_URL,
                    params={
                        "get": "NAME",
                        "for": f"county:{county_fips}",
                        "in": f"state:{state_fips}",
                    },
                )
                resp.raise_for_status()
                rows = resp.json()

            # rows = [["NAME", "state", "county"], ["King County, Washington", "53", "033"]]
            if len(rows) < 2:
                logger.warning("Census API: no county for state=%s county=%s", state_fips, county_fips)
                return None

            return rows[1][0]

        except Exception as e:
            if attempt == 0:
                logger.debug("Census county lookup failed (state=%s county=%s), retrying: %s", state_fips, county_fips, e or type(e).__name__)
            else:
                logger.warning("Census county name lookup failed (state=%s county=%s): %s", state_fips, county_fips, e or type(e).__name__)
    return None
