"""
Google Maps Geocoding — resolves lat/lon from a street address.

Requires GOOGLE_MAPS_KEY env var (free up to 40,000 calls/month).
Degrades gracefully when the key is absent or the request fails.

Results are cached in-memory for the lifetime of the process so each
unique address is only geocoded once per pipeline run.
"""
import logging
import os

import httpx

logger = logging.getLogger(__name__)

_GOOGLE_MAPS_KEY = os.getenv("GOOGLE_MAPS_KEY")
_GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"
_TIMEOUT = 5.0

# In-process cache: address (lowercased) → (lat, lon) or None
_cache: dict[str, tuple[float, float] | None] = {}


async def geocode_address(
    address: str,
    client: httpx.AsyncClient,
) -> tuple[float, float] | None:
    """
    Return (latitude, longitude) for an address, or None on any failure.

    Results are cached — calling this multiple times with the same address
    hits the network only once per process run.
    """
    if not _GOOGLE_MAPS_KEY:
        return None

    key = address.strip().lower()
    if key in _cache:
        return _cache[key]

    try:
        resp = await client.get(
            _GEOCODE_URL,
            params={"address": address, "key": _GOOGLE_MAPS_KEY},
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        results = data.get("results", [])
        if not results:
            logger.debug("Geocoding returned no results for: %s", address)
            _cache[key] = None
            return None

        loc = results[0]["geometry"]["location"]
        result = (loc["lat"], loc["lng"])
        _cache[key] = result
        return result

    except Exception as e:
        logger.debug("Geocoding failed for '%s': %s", address, e)
        _cache[key] = None
        return None