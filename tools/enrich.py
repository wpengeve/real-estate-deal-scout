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

from tools.assessor import lookup_parcel
from tools.crosswalk import zip_to_county
from tools.models import EnrichConfig, EnrichResult, RawListing
from tools.redfin_scraper import fetch_listing_features
from tools.schools import enrich_with_proficiency, fetch_nearby_schools
from tools.solar import fetch_solar_ghi

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
_hud_entity_id: str | None = None                      # county fips_code (looked up once)
_hud_fmr_data: dict[str, dict[str, int]] | None = None # zip_code → {bedroom: rent}
                                                        # key "MSA level" = county-wide fallback

# Lazy lock — created on first use (after the event loop is running)
_hud_init_lock: asyncio.Lock | None = None


def _get_hud_lock() -> asyncio.Lock:
    global _hud_init_lock
    if _hud_init_lock is None:
        _hud_init_lock = asyncio.Lock()
    return _hud_init_lock

_HUD_BED_KEYS = {
    1: "One-Bedroom",
    2: "Two-Bedroom",
    3: "Three-Bedroom",
    4: "Four-Bedroom",
}

# State FIPS → postal abbreviation (needed because HUD listCounties uses abbreviations)
_FIPS_TO_STATE_ABBR: dict[str, str] = {
    "01": "AL", "02": "AK", "04": "AZ", "05": "AR", "06": "CA",
    "08": "CO", "09": "CT", "10": "DE", "11": "DC", "12": "FL",
    "13": "GA", "15": "HI", "16": "ID", "17": "IL", "18": "IN",
    "19": "IA", "20": "KS", "21": "KY", "22": "LA", "23": "ME",
    "24": "MD", "25": "MA", "26": "MI", "27": "MN", "28": "MS",
    "29": "MO", "30": "MT", "31": "NE", "32": "NV", "33": "NH",
    "34": "NJ", "35": "NM", "36": "NY", "37": "NC", "38": "ND",
    "39": "OH", "40": "OK", "41": "OR", "42": "PA", "44": "RI",
    "45": "SC", "46": "SD", "47": "TN", "48": "TX", "49": "UT",
    "50": "VT", "51": "VA", "53": "WA", "54": "WV", "55": "WI",
    "56": "WY",
}


# ── Public API ─────────────────────────────────────────────────────────────────

async def enrich_neighborhood(
    listing: RawListing,
    enrich_config: EnrichConfig | None = None,
) -> EnrichResult:
    """
    Enrich a listing concurrently: Walk Score, HUD rent, KC Assessor, and nearby
    schools all run in parallel via asyncio.gather.

    Always returns EnrichResult (never raises).
    Fields are None when the data source is unavailable or unconfigured.
    """
    async def _get_rent() -> float | None:
        if listing.estimated_monthly_rent is not None:
            return listing.estimated_monthly_rent
        if _HUD_API_KEY and listing.beds:
            rent = await _fetch_hud_fmr_rent(listing.beds, listing.address, enrich_config)
            if rent and enrich_config and enrich_config.hud_rent_multiplier != 1.0:
                rent = round(rent * enrich_config.hud_rent_multiplier)
            return rent
        return None

    async def _get_parcel() -> dict | None:
        if listing.tax_assessed_value is None or listing.zoning is None:
            return await lookup_parcel(listing.address)
        return None

    async def _get_schools() -> list | None:
        # Return None = "no update needed" (already set or no coordinates)
        if listing.nearby_schools is not None:
            return None
        if not listing.latitude or not listing.longitude:
            return None
        schools = await fetch_nearby_schools(listing.latitude, listing.longitude)
        if schools:
            schools = await enrich_with_proficiency(schools)
        return schools or []

    async def _get_solar() -> float | None:
        if listing.solar_ghi_annual is not None:
            return listing.solar_ghi_annual
        if listing.latitude and listing.longitude:
            return await fetch_solar_ghi(listing.latitude, listing.longitude)
        return None

    walk_score, rent, parcel, schools, solar_ghi, listing_features = await asyncio.gather(
        _fetch_walk_score(listing),
        _get_rent(),
        _get_parcel(),
        _get_schools(),
        _get_solar(),
        fetch_listing_features(listing.listing_url or "", _shared_client()),
    )

    updated_listing = listing
    if parcel:
        updated_listing = listing.model_copy(update={
            k: v for k, v in parcel.items()
            if v is not None and getattr(listing, k) is None
        })
    if schools is not None:
        updated_listing = updated_listing.model_copy(update={"nearby_schools": schools})
    if solar_ghi is not None:
        updated_listing = updated_listing.model_copy(update={"solar_ghi_annual": solar_ghi})

    lf = listing_features
    # Prefer scraped walk score (from Redfin page) over Walk Score API result.
    # Scraped scores are free and don't need a separate API key.
    effective_walk_score = (lf.walk_score if lf and lf.walk_score is not None else walk_score)

    # Backfill year_built and photo_url from scraper
    backfill = {}
    if lf and lf.year_built is not None and updated_listing.year_built is None:
        backfill["year_built"] = lf.year_built
    if lf and lf.photo_url is not None and updated_listing.photo_url is None:
        backfill["photo_url"] = lf.photo_url
    if backfill:
        updated_listing = updated_listing.model_copy(update=backfill)

    return EnrichResult(
        listing=updated_listing,
        walk_score=effective_walk_score,
        bike_score=lf.bike_score if lf else None,
        transit_score=lf.transit_score if lf else None,
        estimated_monthly_rent=rent,
        has_primary_suite=lf.has_primary_suite if lf else None,
        has_garage=lf.has_garage if lf else None,
        garage_spaces=lf.garage_spaces if lf else None,
        has_basement=lf.has_basement if lf else None,
        basement_finished=lf.basement_finished if lf else None,
        has_fireplace=lf.has_fireplace if lf else None,
        site_features=lf.site_features if lf else [],
        lot_features=lf.lot_features if lf else [],
        listing_remarks=lf.remarks if lf else None,
    )


_ENRICH_CONCURRENCY = 5   # max parallel per-listing API calls (reduced: scraper adds HTTP)

_http_client: httpx.AsyncClient | None = None


def _shared_client() -> httpx.AsyncClient:
    """Lazy singleton httpx client — reuses connections across all listings."""
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.AsyncClient()
    return _http_client


async def enrich_all(
    listings: list[RawListing],
    enrich_config: EnrichConfig | None = None,
) -> list[EnrichResult]:
    """
    Enrich all listings concurrently (up to _ENRICH_CONCURRENCY at a time).

    A semaphore caps simultaneous outbound requests to avoid rate-limiting
    Walk Score and the KC Assessor APIs. Order of results matches input order.
    """
    sem = asyncio.Semaphore(_ENRICH_CONCURRENCY)

    async def _bounded(listing: RawListing) -> EnrichResult:
        async with sem:
            return await enrich_neighborhood(listing, enrich_config)

    return list(await asyncio.gather(*(_bounded(l) for l in listings)))


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

async def _resolve_county(
    address: str,
    enrich_config: EnrichConfig | None,
) -> tuple[str | None, str | None]:
    """
    Return (state_fips, county_name) using config override or ZIP crosswalk.

    Config takes priority; crosswalk is the fallback when both fields are None.
    """
    if enrich_config and enrich_config.hud_state_fips and enrich_config.hud_county_name:
        return enrich_config.hud_state_fips, enrich_config.hud_county_name

    # Auto-detect from listing ZIP — match after state abbreviation to avoid
    # matching 5-digit house numbers (e.g. "13324 ... WA 98125" → "98125")
    import re
    zip_match = re.search(r",\s*[A-Z]{2}\s+(\d{5})\b", address)
    if not zip_match:
        logger.warning("Cannot auto-detect county: no ZIP in address '%s'", address)
        return None, None

    zip_code = zip_match.group(1)
    result = await zip_to_county(zip_code)
    if result is None:
        logger.warning("ZIP crosswalk returned None for ZIP %s", zip_code)
        return None, None

    return result


async def _fetch_hud_fmr_rent(
    beds: int,
    address: str,
    enrich_config: EnrichConfig | None,
) -> float | None:
    """
    Return the HUD Fair Market Rent (monthly) for the given bedroom count.

    County is resolved in priority order:
      1. enrich_config.hud_state_fips + hud_county_name (explicit override)
      2. Auto-detected via USPS ZIP crosswalk from the listing address ZIP code

    Looks up the county entity ID once, then caches FMR data for the process lifetime.
    Returns None gracefully on any failure.
    """
    global _hud_entity_id, _hud_fmr_data

    # Serialize HUD initialization — prevents concurrent coroutines from making
    # duplicate API calls when _hud_entity_id / _hud_fmr_data are not yet cached.
    async with _get_hud_lock():
        if _hud_entity_id is None:
            state_fips, county_name = await _resolve_county(address, enrich_config)
            if not state_fips or not county_name:
                return None
            _hud_entity_id = await _resolve_hud_entity_id(state_fips, county_name)
            if _hud_entity_id is None:
                return None

        if _hud_fmr_data is None:
            _hud_fmr_data = await _fetch_hud_fmr_data(_hud_entity_id)
            if _hud_fmr_data is None:
                return None

    # Extract ZIP from address — match after state abbreviation to avoid
    # matching 5-digit house numbers (e.g. "13324 ... WA 98125" → "98125")
    import re as _re
    zip_match = _re.search(r",\s*[A-Z]{2}\s+(\d{5})\b", address)
    zip_code = zip_match.group(1) if zip_match else None

    # Look up ZIP-specific rates, fall back to MSA level
    rates = (
        _hud_fmr_data.get(zip_code)
        or _hud_fmr_data.get("MSA level")
        or {}
    )
    if zip_code and zip_code in _hud_fmr_data:
        logger.debug("HUD FMR: using ZIP-level rates for %s", zip_code)
    else:
        logger.debug("HUD FMR: ZIP %s not found, using MSA level", zip_code)

    # Map bedroom count → HUD key (capped at 4BR)
    beds_clamped = min(max(beds, 1), 4)
    key = _HUD_BED_KEYS.get(beds_clamped)
    val = rates.get(key) if key else None
    return float(val) if val else None


async def _resolve_hud_entity_id(state_fips: str, county_name: str) -> str | None:
    """
    Call HUD listCounties to find the fips_code for the target county.

    state_fips: e.g. "53" for Washington (converted internally to "WA")
    county_name: e.g. "King County" — matched as a case-insensitive substring
    """
    state_abbr = _FIPS_TO_STATE_ABBR.get(state_fips)
    if not state_abbr:
        logger.warning("Unknown state FIPS '%s' — cannot look up HUD counties", state_fips)
        return None

    try:
        async with httpx.AsyncClient(timeout=_HUD_TIMEOUT) as client:
            resp = await client.get(
                f"{_HUD_BASE_URL}/listCounties/{state_abbr}",
                headers={"Authorization": f"Bearer {_HUD_API_KEY}"},
            )
            resp.raise_for_status()
            # Response is a list (not wrapped in "data")
            counties = resp.json() if isinstance(resp.json(), list) else resp.json().get("data", [])

        target = county_name.lower()
        for county in counties:
            name = (county.get("county_name") or "").lower()
            if target in name:
                fips_code = county.get("fips_code")
                logger.info("HUD fips_code for %s: %s", county_name, fips_code)
                return fips_code

        logger.warning("HUD county '%s' not found in state %s", county_name, state_abbr)
        return None

    except Exception as e:
        logger.warning("HUD county lookup failed: %s", e)
        return None


async def _fetch_hud_fmr_data(fips_code: str) -> dict[str, dict[str, int]] | None:
    """
    Fetch FMR bedroom rates for a county fips_code.

    Returns a dict mapping zip_code → {bedroom_label: rent}.
    The key "MSA level" holds county-wide fallback rates.

    HUD changed the basicdata format in FY2025:
      - FY2024 and earlier: basicdata is a flat dict {bedroom_label: rent}
        → stored as {"MSA level": basicdata}
      - FY2025+: basicdata is a list of per-ZIP dicts keyed by "zip_code"
        → stored as {zip_code: rates_dict, "MSA level": msa_rates_dict}
    """
    try:
        async with httpx.AsyncClient(timeout=_HUD_TIMEOUT) as client:
            resp = await client.get(
                f"{_HUD_BASE_URL}/data/{fips_code}",
                headers={"Authorization": f"Bearer {_HUD_API_KEY}"},
            )
            resp.raise_for_status()
            basicdata = resp.json().get("data", {}).get("basicdata", {})

        if isinstance(basicdata, list):
            # FY2025 small-area format — index by zip_code
            result: dict[str, dict[str, int]] = {}
            for row in basicdata:
                zip_key = row.get("zip_code", "")
                result[zip_key] = row
            msa = result.get("MSA level", {})
        else:
            # FY2024 and earlier — single MSA-level dict
            result = {"MSA level": basicdata}
            msa = basicdata

        logger.info(
            "HUD FMR loaded: %d ZIP entries, MSA 1BR=$%s 2BR=$%s 3BR=$%s",
            len(result),
            msa.get("One-Bedroom"), msa.get("Two-Bedroom"), msa.get("Three-Bedroom"),
        )
        return result

    except Exception as e:
        logger.warning("HUD FMR data fetch failed for fips_code %s: %s", fips_code, e)
        return None
