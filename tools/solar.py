"""
Solar enrichment via NREL Solar Resource Data API (free).

Returns annual Global Horizontal Irradiance (GHI) in kWh/m²/day for a
lat/lon point. GHI is the standard metric for location-level solar potential.

For Seattle / King County, typical range: 3.5–4.5 kWh/m²/day annual.
Higher = more sun exposure = better solar potential.

API key: free signup at https://developer.nrel.gov/signup/
Add NREL_API_KEY to .env — degraded gracefully (None) if not set.

Score (1–5) calibrated to Pacific Northwest range:
    < 3.5  → 1  (heavily shaded / overcast location)
    3.5–3.7 → 2
    3.7–3.9 → 3
    3.9–4.1 → 4
    ≥ 4.1  → 5  (excellent sun for the PNW)
"""
import logging
import os

import httpx

logger = logging.getLogger(__name__)

_NREL_URL = "https://developer.nrel.gov/api/solar/solar_resource/v1.json"
_NREL_TIMEOUT = 10.0

# In-process cache: (lat_rounded, lon_rounded) → ghi_annual
# NREL resolution is ~4km so rounding to 2dp is safe
_solar_cache: dict[tuple[float, float], float | None] = {}


async def fetch_solar_ghi(lat: float, lon: float) -> float | None:
    """
    Return annual average GHI (kWh/m²/day) for the given coordinates.

    Returns None if no API key, coordinates missing, or request fails.
    Results are cached in-process to avoid duplicate calls for nearby listings.
    """
    # Read lazily so load_dotenv() timing doesn't matter
    api_key = os.getenv("NREL_API_KEY")
    if not api_key:
        return None

    cache_key = (round(lat, 2), round(lon, 2))
    if cache_key in _solar_cache:
        return _solar_cache[cache_key]

    try:
        async with httpx.AsyncClient(timeout=_NREL_TIMEOUT) as client:
            resp = await client.get(
                _NREL_URL,
                params={"api_key": api_key, "lat": lat, "lon": lon},
            )
            resp.raise_for_status()
            outputs = resp.json().get("outputs", {})
            ghi = outputs.get("avg_ghi", {}).get("annual")
            result = float(ghi) if ghi is not None else None
    except Exception as e:
        logger.warning("NREL solar lookup failed (%.4f, %.4f): %r", lat, lon, e)
        result = None

    _solar_cache[cache_key] = result
    return result


def solar_score(ghi: float | None) -> int | None:
    """Convert GHI (kWh/m²/day) to a 1–5 score for PNW properties."""
    if ghi is None:
        return None
    if ghi < 3.5:
        return 1
    if ghi < 3.7:
        return 2
    if ghi < 3.9:
        return 3
    if ghi < 4.1:
        return 4
    return 5
