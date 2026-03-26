"""
School data enrichment using two free, no-key APIs:

  NCES EDGE MapServer  — nearby public school locations by lat/lon
  Urban Institute API  — test score proficiency (math + reading) by NCES school ID

Both degrade gracefully — missing coordinates or API failures return an empty list.
Proficiency results are cached in-process to avoid duplicate Urban Institute calls.

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
    "/K12_School_Locations/EDGE_GEOCODE_PUBLICSCH_2223/MapServer/0/query"
)
_NCES_TIMEOUT = 10.0
_NCES_RADIUS_MILES = 1.5
_NCES_MAX_SCHOOLS = 6

# ── Urban Institute Education Data Portal ─────────────────────────────────────

_URBAN_URL = "https://educationdata.urban.org/api/v1/schools/edfacts/assessments"
_URBAN_YEARS = [2023, 2022, 2019]   # try newest first; edfacts data lags ~2 years
_URBAN_TIMEOUT = 10.0

# in-process cache: nces_id → proficiency score (None = not found)
_proficiency_cache: dict[str, float | None] = {}

# ── NCES level code → label ────────────────────────────────────────────────────

_LEVEL_MAP = {
    1: "Elementary", "1": "Elementary",
    2: "Elementary", "2": "Elementary",
    3: "Middle",     "3": "Middle",
    4: "High",       "4": "High",
    5: "School",     "5": "School",   # K-12 combined
}


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
        "outFields": "NCESSCH,SCHNAM,NAME,LEVEL,LAT,LON",
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
        name = a.get("SCHNAM") or a.get("NAME") or ""
        level = _LEVEL_MAP.get(a.get("LEVEL"), "School")
        s_lat, s_lon = a.get("LAT"), a.get("LON")
        dist = _haversine(lat, lon, s_lat, s_lon) if s_lat and s_lon else None
        schools.append(SchoolInfo(nces_id=nces_id, name=name, level=level, distance_miles=dist))

    schools.sort(key=lambda s: s.distance_miles if s.distance_miles is not None else 999)
    return schools[:_NCES_MAX_SCHOOLS]


async def enrich_with_proficiency(schools: list[SchoolInfo]) -> list[SchoolInfo]:
    """Add proficiency scores to a list of schools (in order, concurrently)."""
    import asyncio
    async def _one(school: SchoolInfo) -> SchoolInfo:
        score = await _fetch_proficiency(school.nces_id)
        return school.model_copy(update={"proficiency_score": score})
    return list(await asyncio.gather(*(_one(s) for s in schools)))


# ── Internal helpers ───────────────────────────────────────────────────────────

async def _fetch_proficiency(nces_id: str) -> float | None:
    """
    Average math + reading proficiency % from Urban Institute edfacts.
    Cached per nces_id. Returns None when no data or on failure.
    """
    if nces_id in _proficiency_cache:
        return _proficiency_cache[nces_id]

    for year in _URBAN_YEARS:
        try:
            async with httpx.AsyncClient(timeout=_URBAN_TIMEOUT) as client:
                resp = await client.get(
                    f"{_URBAN_URL}/{year}/",
                    params={"ncessch": nces_id, "grade_edfacts": 99},
                )
                resp.raise_for_status()
                results = resp.json().get("results", [])

            if not results:
                continue

            scores = [
                v
                for r in results
                for v in (r.get("math_test_pct_prof_midpt"), r.get("read_test_pct_prof_midpt"))
                if v is not None
            ]
            if scores:
                score = round(sum(scores) / len(scores), 1)
                _proficiency_cache[nces_id] = score
                return score

        except Exception as e:
            logger.debug("Urban Institute proficiency lookup failed (%s, %d): %s", nces_id, year, e)

    _proficiency_cache[nces_id] = None
    return None


def _haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distance in miles between two lat/lon points."""
    R = 3958.8
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return R * 2 * atan2(sqrt(a), sqrt(1 - a))