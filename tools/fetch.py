"""
Fetch listings from a data source.

Four backends:
  fixtures   — load from fixtures/listings.json (default, for development)
  csv        — load from a locally saved Redfin CSV (recommended for real data)
  redfin     — fetch live CSV from Redfin's "Download All" endpoint (no API key)
  scraperapi — fetch live structured listings via ScraperAPI's Redfin endpoint
               Requires SCRAPERAPI_KEY and optionally GOOGLE_MAPS_KEY in .env.
               Set fetch.scraperapi_search_urls in config.yaml.

To use the csv backend:
  1. Go to redfin.com and search your city/filters
  2. Click "Download All" (bottom of results page)
  3. Save the file to data/redfin.csv  (or set fetch.csv_path in config.yaml)
  4. Set fetch.data_source: csv in config.yaml
"""
import asyncio
import csv
import hashlib
import io
import json
import logging
import os
import time
from pathlib import Path

import httpx

from tools.geocode import geocode_address
from tools.models import FetchConfig, RawListing
from tools.scraperapi_normalizer import normalize_all

logger = logging.getLogger(__name__)

_SCRAPERAPI_KEY = os.getenv("SCRAPERAPI_KEY")
_SCRAPERAPI_URL = "https://api.scraperapi.com/structured/redfin/search"
_SCRAPERAPI_TIMEOUT = 60.0  # structured endpoints can be slow

# Response cache — avoids burning credits on repeated runs with the same criteria.
# Each structured Redfin call costs 25 credits; caching makes reruns essentially free.
_CACHE_DIR = Path("data/scraperapi_cache")
_CACHE_TTL_SECONDS = int(os.getenv("SCRAPERAPI_CACHE_TTL_HOURS", "6")) * 3600


def _cache_path(url: str) -> Path:
    key = hashlib.md5(url.encode()).hexdigest()
    return _CACHE_DIR / f"{key}.json"


def _cache_get(url: str) -> dict | None:
    """Return cached response JSON if present and not expired, else None."""
    path = _cache_path(url)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        if time.time() - data["cached_at"] > _CACHE_TTL_SECONDS:
            return None
        return data["response"]
    except Exception:
        return None


def _cache_set(url: str, response: dict) -> None:
    """Write response to cache."""
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _cache_path(url).write_text(json.dumps({"cached_at": time.time(), "response": response}))

_DEFAULT_FIXTURES = Path(__file__).parent.parent / "fixtures" / "listings.json"

# Redfin's internal CSV download endpoint (same one used by the website's Download All button)
_REDFIN_CSV_URL = "https://www.redfin.com/stingray/api/gis-csv"
_REDFIN_AUTOCOMPLETE_URL = "https://www.redfin.com/stingray/do/location-autocomplete"
_REDFIN_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# Our home_type names → Redfin URL filter slugs
_HOME_TYPE_TO_REDFIN_SLUG = {
    "Single Family": "house",
    "Condo": "condo",
    "Townhouse": "townhouse",
    "Multi-Family": "multifamily",
}

# Redfin PROPERTY TYPE column → our home_type
_HOME_TYPE_MAP = {
    "Single Family Residential": "Single Family",
    "Condo/Co-op": "Condo",
    "Townhouse": "Townhouse",
    "Multi-Family (2-4 Unit)": "Multi-Family",
    "Manufactured In Park": "Manufactured",
    "Vacant Land": "Land",
}


def fetch_listings(
    market: str,
    fetch_config: FetchConfig | None = None,
) -> list[RawListing]:
    """
    Return raw listings for the given market.

    Dispatches to Redfin CSV or fixtures based on fetch_config.data_source.

    Raises:
        FileNotFoundError: fixtures file is missing (fixtures mode)
        ValueError:        fixtures file contains invalid JSON (fixtures mode)
        RuntimeError:      Redfin request failed (redfin mode)
    """
    if fetch_config and fetch_config.data_source == "scraperapi":
        # ScraperAPI requires async I/O. Callers inside a running event loop
        # (e.g. pipeline.py) must use fetch_listings_async() instead.
        raise RuntimeError(
            "data_source=scraperapi requires an async caller. "
            "Use `await fetch_listings_async(market, config)` from an async context."
        )

    if fetch_config and fetch_config.data_source == "redfin":
        logger.info("Fetching live listings from Redfin for %s", market)
        return _fetch_from_redfin(fetch_config)

    if fetch_config and fetch_config.data_source == "csv":
        paths = fetch_config.csv_paths or [fetch_config.csv_path]
        if len(paths) == 1:
            logger.info("Loading listings from CSV: %s", paths[0])
            return _load_from_csv(paths[0])
        logger.info("Loading listings from %d CSV files", len(paths))
        return _load_from_multiple_csvs(paths)

    logger.info("Loading fixture listings for %s", market)
    return _load_fixtures()


async def fetch_listings_async(
    market: str,
    fetch_config: FetchConfig | None = None,
) -> list[RawListing]:
    """
    Async variant of fetch_listings — required for data_source=scraperapi.

    For all other backends this delegates to the sync fetch_listings() so
    callers don't need to branch on data source.
    """
    if fetch_config and fetch_config.data_source == "scraperapi":
        logger.info("Fetching live listings from ScraperAPI for %s", market)
        return await _fetch_from_scraperapi(fetch_config)
    return fetch_listings(market, fetch_config)


# ── Fixtures backend ───────────────────────────────────────────────────────────

def _load_fixtures(fixtures_path: Path = _DEFAULT_FIXTURES) -> list[RawListing]:
    if not fixtures_path.exists():
        raise FileNotFoundError(
            f"Fixtures file not found: {fixtures_path}\n"
            "Set fetch.data_source: redfin in config.yaml to use live data."
        )
    try:
        data = json.loads(fixtures_path.read_text())
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON in fixtures file: {e}") from e

    if not isinstance(data, list):
        raise ValueError("Fixtures file must contain a JSON array of listings.")

    listings = []
    for i, item in enumerate(data):
        try:
            listings.append(RawListing.model_validate(item))
        except Exception as e:
            raise ValueError(f"Invalid listing at index {i}: {e}") from e
    return listings


# ── Local CSV backend ──────────────────────────────────────────────────────────

def _load_from_csv(csv_path: str) -> list[RawListing]:
    """
    Load listings from a manually downloaded Redfin CSV file.

    Drop your Redfin "Download All" CSV at data/redfin.csv (or set fetch.csv_path).
    Same format as the live Redfin endpoint — no transformation needed.
    """
    path = Path(csv_path)
    if not path.exists():
        raise FileNotFoundError(
            f"CSV file not found: {csv_path}\n"
            "Download it from redfin.com → search your area → Download All,\n"
            "then save it to data/redfin.csv (or set fetch.csv_path in config.yaml)."
        )
    listings = _parse_redfin_csv(path.read_text(encoding="utf-8-sig"))
    logger.info("Parsed %d listings from %s", len(listings), csv_path)
    return listings


def _load_from_multiple_csvs(paths: list[str]) -> list[RawListing]:
    """
    Load and merge listings from multiple Redfin CSV files.

    Duplicates are removed by address (case-insensitive). When the same
    address appears in more than one file, the first occurrence wins.
    """
    seen: set[str] = set()
    merged: list[RawListing] = []

    for path in paths:
        listings = _load_from_csv(path)
        before = len(merged)
        for listing in listings:
            key = listing.address.strip().lower()
            if key not in seen:
                seen.add(key)
                merged.append(listing)
        added = len(merged) - before
        dupes = len(listings) - added
        logger.info(
            "  %s: %d listings loaded, %d duplicate%s skipped",
            path, len(listings), dupes, "s" if dupes != 1 else "",
        )

    logger.info("Merged total: %d unique listings from %d files", len(merged), len(paths))
    return merged


# ── Redfin CSV backend ─────────────────────────────────────────────────────────

def _fetch_from_redfin(fetch_config: FetchConfig) -> list[RawListing]:
    """
    Download active For Sale listings from Redfin's CSV endpoint.

    Returns up to redfin_max_homes listings (Redfin hard cap: 350).
    """
    params = {
        "al": 1,                                   # active listings only
        "num_homes": fetch_config.redfin_max_homes,
        "ord": "redfin-recommended-asc",
        "page_number": 1,
        "region_id": fetch_config.redfin_region_id,
        "region_type": fetch_config.redfin_region_type,
        "status": 9,                               # for sale
        "uipt": "1,2,3,4",                         # house, condo, townhouse, multi-family
        "v": 8,
    }

    try:
        response = httpx.get(
            _REDFIN_CSV_URL,
            params=params,
            headers=_REDFIN_HEADERS,
            timeout=30,
            follow_redirects=True,
        )
        response.raise_for_status()
    except httpx.HTTPStatusError as e:
        raise RuntimeError(
            f"Redfin returned HTTP {e.response.status_code}. "
            "Check region_id and region_type in config.yaml.\n"
            "Find yours: redfin.com → search your area → Download All → copy URL."
        ) from e
    except httpx.TimeoutException as e:
        raise RuntimeError("Redfin request timed out after 30s.") from e

    listings = _parse_redfin_csv(response.text)
    logger.info("Parsed %d listings from Redfin CSV", len(listings))
    return listings


def _parse_redfin_csv(csv_text: str) -> list[RawListing]:
    """Parse Redfin's CSV export format into RawListing objects."""
    listings = []
    reader = csv.DictReader(io.StringIO(csv_text))
    for i, row in enumerate(reader):
        listing = _row_to_listing(row, fallback_zpid=str(i + 1))
        if listing:
            listings.append(listing)
    return listings


def _row_to_listing(row: dict, fallback_zpid: str) -> RawListing | None:
    """Convert one Redfin CSV row to a RawListing. Returns None on parse failure."""
    try:
        address = (row.get("ADDRESS") or "").strip()
        city = (row.get("CITY") or "").strip()
        state = (row.get("STATE OR PROVINCE") or "").strip()
        zip_code = (row.get("ZIP OR POSTAL CODE") or "").strip()
        full_address = f"{address}, {city}, {state} {zip_code}"

        # Use Redfin URL slug as stable ID; fall back to row index
        url = _get_url_field(row)
        zpid = url.rstrip("/").split("/")[-1] if url else fallback_zpid

        hoa = _float_or_none(row.get("HOA/MONTH"))

        return RawListing(
            zpid=zpid,
            address=full_address,
            price=_float_or_none(row.get("PRICE")),
            beds=_int_or_none(row.get("BEDS")),
            baths=_float_or_none(row.get("BATHS")),
            sqft=_int_or_none(row.get("SQUARE FEET")),
            lot_sqft=_int_or_none(row.get("LOT SIZE")),
            home_type=_HOME_TYPE_MAP.get((row.get("PROPERTY TYPE") or "").strip()),
            hoa_fee=hoa if hoa and hoa > 0 else None,
            days_on_market=_int_or_none(row.get("DAYS ON MARKET")),
            latitude=_float_or_none(row.get("LATITUDE")),
            longitude=_float_or_none(row.get("LONGITUDE")),
            listing_url=url or None,
            year_built=_int_or_none(row.get("YEAR BUILT")),
        )
    except Exception as exc:
        logger.warning("Skipping malformed Redfin row: %s", exc)
        return None


def _get_url_field(row: dict) -> str:
    """Redfin's URL column header is very long — find it by prefix."""
    for key in row:
        if key.startswith("URL"):
            return (row[key] or "").strip()
    return ""


def _float_or_none(val: str | None) -> float | None:
    if not val or val.strip() in ("", "—", "N/A", "-"):
        return None
    try:
        return float(val.replace(",", "").replace("$", "").strip())
    except ValueError:
        return None


def _int_or_none(val: str | None) -> int | None:
    f = _float_or_none(val)
    return int(f) if f is not None else None


# ── Redfin city URL auto-resolution ───────────────────────────────────────────

# Pre-verified Redfin city IDs for major US markets.
# Format: lowercase city name → (city_id, url_path)
# IDs come from the canonical Redfin city URL:  redfin.com/city/{ID}/{PATH}
# Verified sources: config.yaml (Seattle), Redfin public search pages.
# Add new entries as users discover missing cities.
_REDFIN_CITY_TABLE: dict[str, tuple[str, str]] = {
    # ── Washington ──
    "seattle": ("16163", "WA/Seattle"),
    "bellevue": ("1778", "WA/Bellevue"),
    "kirkland": ("9065", "WA/Kirkland"),
    "redmond": ("15468", "WA/Redmond"),
    "shoreline": ("16453", "WA/Shoreline"),
    "bothell": ("2152", "WA/Bothell"),
    "kenmore": ("8905", "WA/Kenmore"),
    "lynnwood": ("11348", "WA/Lynnwood"),
    "edmonds": ("4797", "WA/Edmonds"),
    "renton": ("15631", "WA/Renton"),
    "burien": ("2429", "WA/Burien"),
    "tukwila": ("18318", "WA/Tukwila"),
    "mercer island": ("11842", "WA/Mercer-Island"),
    "issaquah": ("8458", "WA/Issaquah"),
    "sammamish": ("30459", "WA/Sammamish"),
    "mountlake terrace": ("12885", "WA/Mountlake-Terrace"),
    "tacoma": ("17782", "WA/Tacoma"),
    "bellevue wa": ("1778", "WA/Bellevue"),
    # ── California ──
    "los angeles": ("11203", "CA/Los-Angeles"),
    "san francisco": ("17151", "CA/San-Francisco"),
    "san jose": ("17189", "CA/San-Jose"),
    "san diego": ("16904", "CA/San-Diego"),
    "oakland": ("14025", "CA/Oakland"),
    "sacramento": ("16006", "CA/Sacramento"),
    "long beach": ("11153", "CA/Long-Beach"),
    "fresno": ("6122", "CA/Fresno"),
    "anaheim": ("576", "CA/Anaheim"),
    "irvine": ("8476", "CA/Irvine"),
    "santa ana": ("17430", "CA/Santa-Ana"),
    "riverside": ("15772", "CA/Riverside"),
    "pasadena": ("15141", "CA/Pasadena"),
    "glendale": ("6568", "CA/Glendale"),
    "burbank": ("2418", "CA/Burbank"),
    "torrance": ("18162", "CA/Torrance"),
    "santa monica": ("17447", "CA/Santa-Monica"),
    "culver city": ("3636", "CA/Culver-City"),
    "el monte": ("4917", "CA/El-Monte"),
    "pomona": ("15300", "CA/Pomona"),
    "sunnyvale": ("17573", "CA/Sunnyvale"),
    "santa clara": ("17387", "CA/Santa-Clara"),
    "palo alto": ("15113", "CA/Palo-Alto"),
    "mountain view": ("12943", "CA/Mountain-View"),
    "fremont": ("6079", "CA/Fremont"),
    "berkeley": ("1945", "CA/Berkeley"),
    "hayward": ("7499", "CA/Hayward"),
    "chula vista": ("2965", "CA/Chula-Vista"),
    "escondido": ("5164", "CA/Escondido"),
    "el cajon": ("4907", "CA/El-Cajon"),
    # ── Texas ──
    "houston": ("7925", "TX/Houston"),
    "dallas": ("4368", "TX/Dallas"),
    "austin": ("30818", "TX/Austin"),
    "san antonio": ("17001", "TX/San-Antonio"),
    "fort worth": ("5892", "TX/Fort-Worth"),
    "el paso": ("4930", "TX/El-Paso"),
    "arlington": ("731", "TX/Arlington"),
    "corpus christi": ("3520", "TX/Corpus-Christi"),
    "plano": ("15269", "TX/Plano"),
    "lubbock": ("11309", "TX/Lubbock"),
    # ── Florida ──
    "miami": ("11953", "FL/Miami"),
    "orlando": ("14566", "FL/Orlando"),
    "tampa": ("17881", "FL/Tampa"),
    "jacksonville": ("8574", "FL/Jacksonville"),
    "st. petersburg": ("17150", "FL/St-Petersburg"),
    "st petersburg": ("17150", "FL/St-Petersburg"),
    "hialeah": ("7638", "FL/Hialeah"),
    "fort lauderdale": ("5861", "FL/Fort-Lauderdale"),
    "tallahassee": ("17837", "FL/Tallahassee"),
    "boca raton": ("2078", "FL/Boca-Raton"),
    "pembroke pines": ("15162", "FL/Pembroke-Pines"),
    # ── New York ──
    "new york city": ("30749", "NY/New-York-City"),
    "new york": ("30749", "NY/New-York-City"),
    "brooklyn": ("9279", "NY/Brooklyn"),
    "queens": ("15303", "NY/Queens"),
    "bronx": ("2265", "NY/Bronx"),
    "staten island": ("17113", "NY/Staten-Island"),
    "buffalo": ("2394", "NY/Buffalo"),
    "rochester": ("15826", "NY/Rochester"),
    "yonkers": ("19638", "NY/Yonkers"),
    # ── Illinois ──
    "chicago": ("29470", "IL/Chicago"),
    "aurora": ("786", "IL/Aurora"),
    "rockford": ("15833", "IL/Rockford"),
    "joliet": ("8697", "IL/Joliet"),
    "naperville": ("13046", "IL/Naperville"),
    # ── Arizona ──
    "phoenix": ("15283", "AZ/Phoenix"),
    "tucson": ("18245", "AZ/Tucson"),
    "scottsdale": ("17283", "AZ/Scottsdale"),
    "mesa": ("11709", "AZ/Mesa"),
    "tempe": ("17925", "AZ/Tempe"),
    "chandler": ("2741", "AZ/Chandler"),
    "gilbert": ("6397", "AZ/Gilbert"),
    "glendale az": ("6569", "AZ/Glendale"),
    "glendale arizona": ("6569", "AZ/Glendale"),
    # ── Colorado ──
    "denver": ("29490", "CO/Denver"),
    "colorado springs": ("3217", "CO/Colorado-Springs"),
    "aurora co": ("785", "CO/Aurora"),
    "fort collins": ("5883", "CO/Fort-Collins"),
    "lakewood": ("10607", "CO/Lakewood"),
    # ── Georgia ──
    "atlanta": ("24694", "GA/Atlanta"),
    "columbus": ("26174", "GA/Columbus"),
    "savannah": ("17206", "GA/Savannah"),
    # ── North Carolina ──
    "charlotte": ("23893", "NC/Charlotte"),
    "raleigh": ("27611", "NC/Raleigh"),
    "greensboro": ("25566", "NC/Greensboro"),
    "durham": ("24924", "NC/Durham"),
    # ── Ohio ──
    "columbus oh": ("26174", "OH/Columbus"),
    "cleveland": ("26095", "OH/Cleveland"),
    "cincinnati": ("26012", "OH/Cincinnati"),
    "toledo": ("27531", "OH/Toledo"),
    "akron": ("23695", "OH/Akron"),
    # ── Michigan ──
    "detroit": ("29606", "MI/Detroit"),
    "grand rapids": ("25389", "MI/Grand-Rapids"),
    "warren": ("27763", "MI/Warren"),
    # ── Pennsylvania ──
    "philadelphia": ("26941", "PA/Philadelphia"),
    "pittsburgh": ("26982", "PA/Pittsburgh"),
    "allentown": ("23718", "PA/Allentown"),
    # ── Massachusetts ──
    "boston": ("9189", "MA/Boston"),
    "worcester": ("19512", "MA/Worcester"),
    "cambridge": ("2621", "MA/Cambridge"),
    "springfield": ("17139", "MA/Springfield"),
    "lowell": ("11220", "MA/Lowell"),
    # ── Tennessee ──
    "nashville": ("27274", "TN/Nashville"),
    "memphis": ("26695", "TN/Memphis"),
    "knoxville": ("26587", "TN/Knoxville"),
    # ── Oregon ──
    "portland": ("15338", "OR/Portland"),
    "eugene": ("5236", "OR/Eugene"),
    "salem": ("16129", "OR/Salem"),
    # ── Nevada ──
    "las vegas": ("10884", "NV/Las-Vegas"),
    "henderson": ("7555", "NV/Henderson"),
    "reno": ("15600", "NV/Reno"),
    # ── Missouri ──
    "kansas city": ("26574", "MO/Kansas-City"),
    "st. louis": ("26873", "MO/St-Louis"),
    "st louis": ("26873", "MO/St-Louis"),
    # ── Minnesota ──
    "minneapolis": ("26765", "MN/Minneapolis"),
    "st. paul": ("27490", "MN/St-Paul"),
    "st paul": ("27490", "MN/St-Paul"),
    # ── Indiana ──
    "indianapolis": ("26494", "IN/Indianapolis"),
    "fort wayne": ("25285", "IN/Fort-Wayne"),
    # ── Wisconsin ──
    "milwaukee": ("26746", "WI/Milwaukee"),
    "madison": ("26674", "WI/Madison"),
    # ── Maryland ──
    "baltimore": ("24862", "MD/Baltimore"),
    # ── Virginia ──
    "virginia beach": ("27747", "VA/Virginia-Beach"),
    "norfolk": ("27387", "VA/Norfolk"),
    "richmond": ("27451", "VA/Richmond"),
    "arlington va": ("24627", "VA/Arlington"),
    "arlington virginia": ("24627", "VA/Arlington"),
    "alexandria": ("24599", "VA/Alexandria"),
    # ── Kentucky ──
    "louisville": ("26652", "KY/Louisville"),
    "lexington": ("26608", "KY/Lexington"),
    # ── Louisiana ──
    "new orleans": ("27342", "LA/New-Orleans"),
    "baton rouge": ("24883", "LA/Baton-Rouge"),
    # ── Oklahoma ──
    "oklahoma city": ("27441", "OK/Oklahoma-City"),
    "tulsa": ("27583", "OK/Tulsa"),
    # ── Kansas ──
    "wichita": ("27826", "KS/Wichita"),
    # ── Utah ──
    "salt lake city": ("16140", "UT/Salt-Lake-City"),
    "west valley city": ("19271", "UT/West-Valley-City"),
    "provo": ("15399", "UT/Provo"),
    # ── New Mexico ──
    "albuquerque": ("591", "NM/Albuquerque"),
    # ── Hawaii ──
    "honolulu": ("7786", "HI/Honolulu"),
    # ── South Carolina ──
    "columbia": ("26101", "SC/Columbia"),
    "charleston": ("23942", "SC/Charleston"),
    # ── Alabama ──
    "birmingham": ("24931", "AL/Birmingham"),
    "montgomery": ("26803", "AL/Montgomery"),
    # ── Mississippi ──
    "jackson": ("26546", "MS/Jackson"),
    # ── Arkansas ──
    "little rock": ("26631", "AR/Little-Rock"),
    # ── Nebraska ──
    "omaha": ("27424", "NE/Omaha"),
    "lincoln": ("26621", "NE/Lincoln"),
    # ── Iowa ──
    "des moines": ("25001", "IA/Des-Moines"),
    # ── Connecticut ──
    "bridgeport": ("2246", "CT/Bridgeport"),
    "new haven": ("13180", "CT/New-Haven"),
    "hartford": ("7377", "CT/Hartford"),
    # ── Rhode Island ──
    "providence": ("15407", "RI/Providence"),
    # ── New Hampshire ──
    "manchester": ("11527", "NH/Manchester"),
    # ── Idaho ──
    "boise": ("2098", "ID/Boise"),
    # ── Montana ──
    "billings": ("1979", "MT/Billings"),
}


async def resolve_city_urls(
    cities: list[str],
    max_price: float,
    min_beds: int,
    home_types: list[str] | None = None,
    max_cities: int = 3,
) -> list[str]:
    """
    Auto-resolve Redfin search URLs from plain city names.

    Strategy:
      1. Check _REDFIN_CITY_TABLE (fast, free, covers ~150 major US cities).
      2. For cities not in the table, use ScraperAPI to call Redfin's autocomplete
         endpoint (bypasses 403, costs 1 credit per city lookup).

    Args:
        cities:     City names to resolve (e.g. ["Los Angeles", "Pasadena"]).
        max_price:  Maximum listing price in USD.
        min_beds:   Minimum number of bedrooms.
        home_types: Optional list of property types to filter by.
        max_cities: Cap on the number of URLs returned.

    Returns:
        List of Redfin filter URLs. May be shorter than `cities` if some can't
        be resolved. Returns [] on total failure.
    """
    type_slugs = [_HOME_TYPE_TO_REDFIN_SLUG[ht] for ht in (home_types or []) if ht in _HOME_TYPE_TO_REDFIN_SLUG]
    filter_parts = [f"min-beds={min_beds}", f"max-price={int(max_price)}"]
    if type_slugs:
        filter_parts.append("property-type=" + "+".join(type_slugs))
    filter_str = ",".join(filter_parts)

    resolved: list[str] = []

    async with httpx.AsyncClient(timeout=45.0, headers=_REDFIN_HEADERS) as client:
        for city in cities:
            if len(resolved) >= max_cities:
                break

            city_id, city_path = _lookup_city(city)

            # If not in local table, try ScraperAPI autocomplete as fallback
            if not city_id and _SCRAPERAPI_KEY:
                city_id, city_path = await _scraperapi_autocomplete(city, client)

            if city_id and city_path:
                url = f"https://www.redfin.com/city/{city_id}/{city_path}/filter/{filter_str}"
                resolved.append(url)
                logger.info("Resolved %r → %s", city, url)
            else:
                logger.warning("Could not resolve Redfin city ID for %r — skipping", city)

    return resolved


def _lookup_city(city: str) -> tuple[str, str]:
    """Look up a city in the hardcoded table. Returns (city_id, path) or ('', '')."""
    key = city.strip().lower()
    entry = _REDFIN_CITY_TABLE.get(key)
    if entry:
        return entry
    # Try without common suffixes like ", CA" → "los angeles"
    if "," in key:
        key = key.split(",")[0].strip()
        entry = _REDFIN_CITY_TABLE.get(key)
        if entry:
            return entry
    return "", ""


async def resolve_address_to_url(address: str) -> str | None:
    """
    Resolve a plain address string to a Redfin listing URL.

    Uses DuckDuckGo HTML search (site:redfin.com) to find the listing URL.
    No API key required — DuckDuckGo allows programmatic use without bot detection.
    Returns the full Redfin URL or None if not found.
    """
    from urllib.parse import unquote
    import re as _re

    query = f"redfin {address.strip()}"
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml",
    }
    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            resp = await client.get(
                "https://html.duckduckgo.com/html/",
                params={"q": query},
                headers=headers,
            )
            # DuckDuckGo encodes result URLs in uddg= query params
            encoded_urls = _re.findall(r"uddg=([^&\"]+)", resp.text)
            for enc in encoded_urls:
                url = unquote(enc)
                if "redfin.com" in url and any(
                    seg in url for seg in ["/home/", "/condo/", "/townhouse/", "/other/"]
                ):
                    logger.info("Resolved address %r → %s", address, url)
                    return url
    except Exception as e:
        logger.warning("Address resolution failed for %r: %s", address, e)
    return None


async def _scraperapi_autocomplete(city: str, client: httpx.AsyncClient) -> tuple[str, str]:
    """
    Use ScraperAPI to call Redfin's location-autocomplete endpoint.
    Redfin blocks direct calls (403); ScraperAPI bypasses bot detection.
    Returns (city_id, path) or ('', '') on failure.
    """
    try:
        autocomplete_url = (
            f"{_REDFIN_AUTOCOMPLETE_URL}"
            f"?location={city.replace(' ', '+')}&v=2"
        )
        resp = await client.get(
            "https://api.scraperapi.com/",
            params={"api_key": _SCRAPERAPI_KEY, "url": autocomplete_url},
        )
        resp.raise_for_status()
        data = resp.json()
        results = (data.get("payload") or {}).get("resultList") or []
        city_result = next(
            (r for r in results if str(r.get("id", "")).startswith("city_")),
            None,
        )
        if city_result:
            city_id = str(city_result["id"]).replace("city_", "")
            city_path = str(city_result.get("url", "")).strip("/")
            if city_id and city_path:
                logger.info("ScraperAPI autocomplete resolved %r → city/%s/%s", city, city_id, city_path)
                return city_id, city_path
    except Exception as exc:
        logger.warning("ScraperAPI autocomplete failed for %r: %s", city, exc)
    return "", ""


# ── ScraperAPI backend ─────────────────────────────────────────────────────────

async def _fetch_from_scraperapi(fetch_config: FetchConfig) -> list[RawListing]:
    """
    Fetch structured Redfin listings from ScraperAPI.

    Each URL in scraperapi_search_urls is a Redfin search page URL.
    ScraperAPI scrapes it and returns structured JSON.

    After normalization, geocoding resolves lat/lon for each listing
    (required by downstream enrichment stages: schools, solar, FEMA).

    Raises RuntimeError if SCRAPERAPI_KEY is not set or no URLs configured.
    """
    if not _SCRAPERAPI_KEY:
        raise RuntimeError(
            "SCRAPERAPI_KEY is not set. Add it to .env.\n"
            "Sign up: https://www.scraperapi.com/"
        )

    urls = fetch_config.scraperapi_search_urls
    if not urls:
        raise RuntimeError(
            "No scraperapi_search_urls configured. "
            "Add at least one Redfin search URL to fetch.scraperapi_search_urls in config.yaml."
        )

    all_listings: list[RawListing] = []
    seen_addresses: set[str] = set()

    async with httpx.AsyncClient(timeout=_SCRAPERAPI_TIMEOUT) as client:
        for search_url in urls:
            cached = _cache_get(search_url)
            if cached is not None:
                logger.info("ScraperAPI: cache hit for %s (skipping API call)", search_url)
                raw_json = cached
            else:
                logger.info("ScraperAPI: fetching %s", search_url)
                try:
                    resp = await client.get(
                        _SCRAPERAPI_URL,
                        params={"api_key": _SCRAPERAPI_KEY, "url": search_url},
                    )
                    resp.raise_for_status()
                except httpx.HTTPStatusError as e:
                    raise RuntimeError(
                        f"ScraperAPI returned HTTP {e.response.status_code} for {search_url}"
                    ) from e
                except httpx.TimeoutException as e:
                    raise RuntimeError(
                        f"ScraperAPI timed out for {search_url}"
                    ) from e
                raw_json = resp.json()
                _cache_set(search_url, raw_json)

            listings = normalize_all(raw_json)
            logger.info("ScraperAPI: %d listings from %s", len(listings), search_url)

            # Deduplicate across multiple search URLs by address
            for listing in listings:
                key = listing.address.strip().lower()
                if key not in seen_addresses:
                    seen_addresses.add(key)
                    all_listings.append(listing)

        # Geocode listings that have no coordinates (ScraperAPI doesn't return lat/lon)
        all_listings = await _geocode_listings(all_listings, client)

    logger.info(
        "ScraperAPI: %d unique listings total (%d geocoded)",
        len(all_listings),
        sum(1 for l in all_listings if l.latitude is not None),
    )
    return all_listings


async def _geocode_listings(
    listings: list[RawListing],
    client: httpx.AsyncClient,
) -> list[RawListing]:
    """
    Resolve lat/lon for listings that are missing coordinates.

    Runs up to 10 geocoding calls concurrently to stay within rate limits.
    Listings that already have coordinates are passed through unchanged.
    """
    sem = asyncio.Semaphore(10)

    async def _resolve(listing: RawListing) -> RawListing:
        if listing.latitude is not None and listing.longitude is not None:
            return listing
        async with sem:
            coords = await geocode_address(listing.address, client)
        if coords:
            return listing.model_copy(update={"latitude": coords[0], "longitude": coords[1]})
        return listing

    return list(await asyncio.gather(*(_resolve(l) for l in listings)))
