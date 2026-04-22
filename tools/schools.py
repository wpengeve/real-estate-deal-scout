"""
School data enrichment using two free, no-key APIs:

  NCES EDGE MapServer  — nearby public school locations by lat/lon
  WA OSPI Open Data    — test score proficiency (ELA + math) by school name

Both degrade gracefully — missing coordinates or API failures return an empty list.
Proficiency results are cached in-process to avoid duplicate API calls.

Note: WA OSPI proficiency data only covers Washington State schools.
Schools in other states will have proficiency_score=None.

Finding the right NCES MapServer URL:
  Go to https://nces.ed.gov/opengis/rest/services/K12_School_Locations/
  and pick the latest EDGE_GEOCODE_PUBLICSCH_XXYY MapServer.
"""
import logging
from math import atan2, cos, radians, sin, sqrt

import httpx

from tools.models import SchoolInfo

logger = logging.getLogger(__name__)

# ── NCES EDGE MapServer ────────────────────────────────────────────────────────

_NCES_URL = (
    "https://nces.ed.gov/opengis/rest/services"
    "/K12_School_Locations/EDGE_GEOCODE_PUBLICSCH_2425/MapServer/0/query"
)
_NCES_TIMEOUT = 10.0
_NCES_RADIUS_MILES = 1.5
_NCES_MAX_SCHOOLS = 6

# ── WA OSPI Open Data — Washington School Improvement Framework ───────────────
# Dataset: https://data.wa.gov/resource/u25x-vdun.json
# Covers WA public schools only. Schools outside WA return None.

_OSPI_URL = "https://data.wa.gov/resource/u25x-vdun.json"
_OSPI_TIMEOUT = 10.0

# in-process cache: school_name (lowercase) → proficiency score (None = not found)
_proficiency_cache: dict[str, float | None] = {}

# ── Level inference from school name ──────────────────────────────────────────
# The 2024-25 NCES schema dropped the LEVEL field; infer from name keywords.

def _infer_level(name: str) -> str:
    n = name.lower()
    if any(k in n for k in ("high school", "senior high", " hs ")):
        return "High"
    if any(k in n for k in ("middle school", "junior high", " ms ")):
        return "Middle"
    if any(k in n for k in ("elementary", "primary", "k-8", "k8")):
        return "Elementary"
    return "School"


# ── Public API ─────────────────────────────────────────────────────────────────

async def fetch_nearby_schools(lat: float, lon: float) -> list[SchoolInfo]:
    """
    Return up to _NCES_MAX_SCHOOLS public schools within _NCES_RADIUS_MILES of
    (lat, lon), sorted by distance.  Empty list on any failure.
    """
    params = {
        "geometry": f"{lon},{lat}",
        "geometryType": "esriGeometryPoint",
        "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
        "distance": _NCES_RADIUS_MILES,
        "units": "esriSRUnit_StatuteMile",
        "outFields": "NCESSCH,NAME,LAT,LON",
        "returnGeometry": "false",
        "f": "json",
    }
    try:
        async with httpx.AsyncClient(timeout=_NCES_TIMEOUT) as client:
            resp = await client.get(_NCES_URL, params=params)
            resp.raise_for_status()
            features = resp.json().get("features", [])
    except Exception as e:
        logger.warning("NCES school lookup failed: %r", e)
        return []

    schools: list[SchoolInfo] = []
    for feat in features:
        a = feat.get("attributes", {})
        nces_id = str(a.get("NCESSCH") or "").zfill(12)
        name = a.get("NAME") or ""
        level = _infer_level(name)
        s_lat, s_lon = a.get("LAT"), a.get("LON")
        dist = _haversine(lat, lon, s_lat, s_lon) if s_lat and s_lon else None
        schools.append(SchoolInfo(nces_id=nces_id, name=name, level=level, distance_miles=dist))

    schools.sort(key=lambda s: s.distance_miles if s.distance_miles is not None else 999)
    return schools[:_NCES_MAX_SCHOOLS]


async def enrich_with_proficiency(schools: list[SchoolInfo]) -> list[SchoolInfo]:
    """Add proficiency scores to a list of schools (in order, concurrently)."""
    import asyncio
    async def _one(school: SchoolInfo) -> SchoolInfo:
        score = await _fetch_proficiency(school.name)
        return school.model_copy(update={"proficiency_score": score})
    return list(await asyncio.gather(*(_one(s) for s in schools)))


# ── Internal helpers ───────────────────────────────────────────────────────────

async def _fetch_proficiency(school_name: str) -> float | None:
    """
    Average ELA + math proficiency % from WA OSPI Open Data (All Students group).
    Cached per school name. Returns None for non-WA schools or on failure.
    """
    cache_key = school_name.lower().strip()
    if cache_key in _proficiency_cache:
        return _proficiency_cache[cache_key]

    try:
        async with httpx.AsyncClient(timeout=_OSPI_TIMEOUT) as client:
            resp = await client.get(
                _OSPI_URL,
                params={
                    "$where": f"school_name='{school_name}' AND student_group='All Students'",
                    "$select": "proficiency_ela_rate,proficiency_math_rate",
                    "$limit": 1,
                },
            )
            resp.raise_for_status()
            results = resp.json()

        if not results:
            _proficiency_cache[cache_key] = None
            return None

        r = results[0]
        ela = _pct_to_float(r.get("proficiency_ela_rate"))
        math = _pct_to_float(r.get("proficiency_math_rate"))
        scores = [v for v in (ela, math) if v is not None]
        score = round(sum(scores) / len(scores), 1) if scores else None
        _proficiency_cache[cache_key] = score
        return score

    except Exception as e:
        logger.debug("OSPI proficiency lookup failed (%s): %s", school_name, e)
        _proficiency_cache[cache_key] = None
        return None


def _pct_to_float(val: str | None) -> float | None:
    """Convert '46.30%' → 46.3, or return None."""
    if not val:
        return None
    try:
        return float(val.rstrip("%"))
    except ValueError:
        return None


def _haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distance in miles between two lat/lon points."""
    R = 3958.8
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return R * 2 * atan2(sqrt(a), sqrt(1 - a))