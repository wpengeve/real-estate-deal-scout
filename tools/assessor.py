"""
Multi-county parcel lookup — King, Snohomish, and Pierce counties (WA).

Routes by ZIP code prefix:
  980, 981 → King County    (gismaps.kingcounty.gov)
  982      → Snohomish Co.  (gis.snoco.org)
  983, 984 → Pierce County  (services2.arcgis.com/1UvBaQ5y1ubjUPmd)

Known limitation: a handful of 980xx ZIPs straddle the King/Snohomish border
(e.g., 98012 Mill Creek = Snohomish County). Those route to King County and
return None gracefully — no crash, just no assessment data for that listing.

All functions are async (httpx.AsyncClient) to support concurrent enrichment.
"""
import logging
import re

import httpx

logger = logging.getLogger(__name__)

# ── API endpoints ──────────────────────────────────────────────────────────────

_KC_URL = (
    "https://gismaps.kingcounty.gov/arcgis/rest/services"
    "/Property/KingCo_PropertyInfo/MapServer/2/query"
)
_SNOCO_URL = (
    "https://gis.snoco.org/sis/rest/services/Cadastral/Tax_Parcels/MapServer/0/query"
)
_PIERCE_URL = (
    "https://services2.arcgis.com/1UvBaQ5y1ubjUPmd/arcgis/rest/services"
    "/Tax_Parcels/FeatureServer/0/query"
)

_TIMEOUT = 10.0

# ── ZIP → county routing ───────────────────────────────────────────────────────

_ZIP_PREFIX_TO_COUNTY: dict[str, str] = {
    "980": "king",
    "981": "king",
    "982": "snohomish",
    "983": "pierce",
    "984": "pierce",
}


def _county_for_zip(zip_code: str) -> str | None:
    """Return 'king', 'snohomish', 'pierce', or None for unrecognized ZIPs."""
    if not zip_code or len(zip_code) < 3:
        return None
    return _ZIP_PREFIX_TO_COUNTY.get(zip_code[:3])


# ── Public API ─────────────────────────────────────────────────────────────────

async def lookup_parcel(address: str) -> dict | None:
    """
    Look up county assessor parcel data for a listing address.

    Dispatches to King, Snohomish, or Pierce County based on ZIP prefix.
    Returns a dict with keys: tax_assessed_value, tax_assessed_land,
    tax_assessed_improvement, zoning — all may be None.

    Returns None (never raises) for unknown ZIPs or any API failure.
    """
    parsed = _parse_address(address)
    if not parsed:
        logger.debug("Could not parse address for assessor lookup: %s", address)
        return None

    house_num, street_prefix, zip_code = parsed
    county = _county_for_zip(zip_code)

    if county == "king":
        return await _lookup_kc(house_num, street_prefix, zip_code)
    if county == "snohomish":
        return await _lookup_snoco(house_num, street_prefix, zip_code)
    if county == "pierce":
        return await _lookup_pierce(house_num, street_prefix, zip_code)

    logger.debug("No assessor configured for ZIP %s (prefix %s)", zip_code, zip_code[:3])
    return None


# ── King County ────────────────────────────────────────────────────────────────

async def _lookup_kc(house_num: str, street_prefix: str, zip_code: str) -> dict | None:
    """
    King County Assessor — gismaps.kingcounty.gov

    ADDR_FULL contains the full street address in uppercase (e.g. "4521 RAINIER AVE S").
    street_prefix includes the house number for the LIKE query.
    """
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(
                _KC_URL,
                params={
                    "where": (
                        f"ADDR_HN='{house_num}' "
                        f"AND ADDR_FULL LIKE '{street_prefix}%' "
                        f"AND ZIP5='{zip_code}'"
                    ),
                    "outFields": "APPRLNDVAL,APPR_IMPR,KCA_ZONING",
                    "f": "json",
                    "returnGeometry": "false",
                    "resultRecordCount": 1,
                },
            )
        resp.raise_for_status()
        features = resp.json().get("features", [])
    except Exception as e:
        logger.warning("King County Assessor error for ZIP %s: %s", zip_code, e)
        return None

    if not features:
        logger.debug("No King County parcel match for ZIP %s", zip_code)
        return None

    attrs = features[0]["attributes"]
    land = float(attrs.get("APPRLNDVAL") or 0)
    impr = float(attrs.get("APPR_IMPR") or 0)
    total = land + impr
    return {
        "tax_assessed_value": total if total > 0 else None,
        "tax_assessed_land": land if land > 0 else None,
        "tax_assessed_improvement": impr if impr > 0 else None,
        "zoning": attrs.get("KCA_ZONING"),
    }


# ── Snohomish County ──────────────────────────────────────────────────────────

async def _lookup_snoco(house_num: str, street_prefix: str, zip_code: str) -> dict | None:
    """
    Snohomish County Assessor — gis.snoco.org

    SITUSHOUSE = house number (exact), SITUSSTRT = street name only (no house number),
    SITUSZIP = 5-digit ZIP.

    Note: Snohomish's USECODE is a use classification, not a zoning code —
    we return zoning=None for now. Zoning lives in a separate GIS layer.
    """
    # street_prefix is "4521 RAINIER AVE" — strip the house number
    street_name = " ".join(street_prefix.split()[1:])

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(
                _SNOCO_URL,
                params={
                    "where": (
                        f"SITUSHOUSE='{house_num}' "
                        f"AND SITUSSTRT LIKE '{street_name}%' "
                        f"AND SITUSZIP='{zip_code}'"
                    ),
                    "outFields": "MKLND,MKIMP,MKTTL",
                    "f": "json",
                    "returnGeometry": "false",
                    "resultRecordCount": 1,
                },
            )
        resp.raise_for_status()
        features = resp.json().get("features", [])
    except Exception as e:
        logger.warning("Snohomish County Assessor error for ZIP %s: %s", zip_code, e)
        return None

    if not features:
        logger.debug("No Snohomish County parcel match for ZIP %s", zip_code)
        return None

    attrs = features[0]["attributes"]
    land = float(attrs.get("MKLND") or 0)
    impr = float(attrs.get("MKIMP") or 0)
    total = float(attrs.get("MKTTL") or 0) or (land + impr)
    return {
        "tax_assessed_value": total if total > 0 else None,
        "tax_assessed_land": land if land > 0 else None,
        "tax_assessed_improvement": impr if impr > 0 else None,
        "zoning": None,  # Snohomish USECODE is use classification, not zoning
    }


# ── Pierce County ─────────────────────────────────────────────────────────────

async def _lookup_pierce(house_num: str, street_prefix: str, zip_code: str) -> dict | None:
    """
    Pierce County Assessor — ArcGIS Online (services2.arcgis.com)

    Site_Address holds the full address string (e.g. "4521 RAINIER AVE S").
    We query using street_prefix (which already includes the house number) as a
    LIKE prefix, plus an exact ZIP match.

    Note: Pierce's Use_Code is a use classification, not a zoning code —
    we return zoning=None for now.
    """
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(
                _PIERCE_URL,
                params={
                    "where": (
                        f"Site_Address LIKE '{street_prefix}%' "
                        f"AND Zipcode='{zip_code}'"
                    ),
                    "outFields": "Land_Value,Improvement_Value",
                    "f": "json",
                    "returnGeometry": "false",
                    "resultRecordCount": 1,
                },
            )
        resp.raise_for_status()
        features = resp.json().get("features", [])
    except Exception as e:
        logger.warning("Pierce County Assessor error for ZIP %s: %s", zip_code, e)
        return None

    if not features:
        logger.debug("No Pierce County parcel match for ZIP %s", zip_code)
        return None

    attrs = features[0]["attributes"]
    land = float(attrs.get("Land_Value") or 0)
    impr = float(attrs.get("Improvement_Value") or 0)
    total = land + impr
    return {
        "tax_assessed_value": total if total > 0 else None,
        "tax_assessed_land": land if land > 0 else None,
        "tax_assessed_improvement": impr if impr > 0 else None,
        "zoning": None,  # Pierce Use_Code is use classification, not zoning
    }


# ── Address parsing ────────────────────────────────────────────────────────────

def _parse_address(address: str) -> tuple[str, str, str] | None:
    """
    Parse a full address into (house_num, street_prefix, zip_code).

    Input:  "4521 Rainier Ave S, Seattle, WA 98118"
    Output: ("4521", "4521 RAINIER AVE", "98118")

    street_prefix includes the house number (used by KC for ADDR_FULL LIKE query).
    Snohomish and Pierce strip the house number from street_prefix inline.
    """
    parts = address.split(",")
    if len(parts) < 3:
        return None

    # Extract ZIP from last segment: " WA 98118" → "98118"
    zip_match = re.search(r"\b(\d{5})\b", parts[-1])
    if not zip_match:
        return None
    zip_code = zip_match.group(1)

    # Normalize street to uppercase: "4521 Rainier Ave S" → "4521 RAINIER AVE S"
    street_upper = parts[0].strip().upper()
    words = street_upper.split()
    if len(words) < 2:
        return None

    house_num = words[0]
    # Use house number + first two street words as LIKE prefix
    # e.g. "4521 RAINIER AVE" — specific enough, tolerant of directional suffix
    prefix_words = words[:3]
    street_prefix = " ".join(prefix_words)

    return house_num, street_prefix, zip_code
