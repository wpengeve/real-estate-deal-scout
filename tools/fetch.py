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
from tools.models import FetchConfig, RawListing, ScreeningCriteria
from tools.scraperapi_normalizer import normalize_all

logger = logging.getLogger(__name__)

_SCRAPERAPI_KEY = os.getenv("SCRAPERAPI_KEY")
_SCRAPERAPI_URL = "https://api.scraperapi.com/structured/redfin/search"
_SCRAPERAPI_TIMEOUT = 60.0  # structured endpoints can be slow

_RENTCAST_KEY = os.getenv("RENTCAST_API_KEY")
_RENTCAST_BASE_URL = "https://api.rentcast.io/v1"

# Rentcast propertyType → our home_type
_RENTCAST_PROP_TYPE_MAP: dict[str, str] = {
    "Single Family": "Single Family",
    "Condo": "Condo",
    "Townhouse": "Townhouse",
    "Multi Family": "Multi-Family",
    "Multi-Family": "Multi-Family",
    "Apartment": "Multi-Family",
}

# Our home_type → Rentcast propertyType (for query params)
_HOME_TYPE_TO_RENTCAST: dict[str, str] = {
    "Single Family": "Single Family",
    "Condo": "Condo",
    "Townhouse": "Townhouse",
    "Multi-Family": "Multi Family",
}

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
    if fetch_config and fetch_config.data_source in ("scraperapi", "rentcast"):
        # These backends require async I/O. Callers inside a running event loop
        # (e.g. pipeline.py) must use fetch_listings_async() instead.
        raise RuntimeError(
            f"data_source={fetch_config.data_source} requires an async caller. "
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
    if fetch_config and fetch_config.data_source == "rentcast":
        logger.info("Fetching live listings from Rentcast for %s", market)
        return await _fetch_from_rentcast(fetch_config)
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


def redfin_filter_string(criteria: ScreeningCriteria) -> str:
    """
    Build the `/filter/...` segment of a Redfin search URL from our criteria.

    Every criterion pushed here is one Redfin excludes server-side. That matters
    more than the saved credit: the structured endpoint returns a single page of
    ~43 listings per call, so a listing that only fails screening later has
    occupied a slot a real candidate could have had.

    Slugs are Redfin's undocumented URL vocabulary, so each one below was
    verified against live Seattle results on 2026-08-18 rather than assumed —
    an unrecognised slug is silently ignored, which looks identical to a filter
    that matched everything:

      min-beds, max-price   already in use
      min-price=1500000     min listing price rose 700,000 → 1,519,000    ✓
      min-baths=2.5         min baths rose 1.5 → 2.5                       ✓
      hoa=0                 43 condo results → 20 ("no HOA fee")          ✓
      hoa=50                43 condo results → 30 (cap is a monthly $)    ✓
      max-hoa=0             IGNORED — result still included a $1,556 HOA  ✗

    Deliberately not pushed: `max_dom`. Redfin only takes coarse buckets
    (1wk/2wk/1mo…), so any mapping either over-filters or is a no-op, and DOM is
    cheap to screen locally.
    """
    parts = [f"min-beds={criteria.min_beds}", f"max-price={int(criteria.max_price)}"]

    if criteria.min_price is not None:
        parts.append(f"min-price={int(criteria.min_price)}")
    if criteria.min_baths is not None:
        # Redfin accepts halves (2.5); trim 2.0 → 2 so the URL matches its own.
        baths = criteria.min_baths
        parts.append(f"min-baths={int(baths) if baths == int(baths) else baths}")
    if criteria.max_hoa_fee is not None:
        # hoa=0 is Redfin's "No HOA fee" option, which is exactly what
        # max_hoa_fee=0 means to us.
        parts.append(f"hoa={int(criteria.max_hoa_fee)}")

    type_slugs = [
        _HOME_TYPE_TO_REDFIN_SLUG[ht]
        for ht in (criteria.preferred_home_types or [])
        if ht in _HOME_TYPE_TO_REDFIN_SLUG
    ]
    if type_slugs:
        parts.append("property-type=" + "+".join(type_slugs))

    return ",".join(parts)


async def resolve_city_urls(
    cities: list[str],
    criteria: ScreeningCriteria,
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
        criteria:   Screening criteria — everything Redfin can filter on is
                    pushed into the URL by redfin_filter_string().
        max_cities: Cap on the number of URLs returned.

    Returns:
        List of Redfin filter URLs. May be shorter than `cities` if some can't
        be resolved. Returns [] on total failure.
    """
    filter_str = redfin_filter_string(criteria)

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

    Strategy:
      1. Redfin autocomplete API via ScraperAPI (exact match, most reliable).
      2. Redfin autocomplete API direct (works if not rate-limited).
      3. Brave Search via ScraperAPI (fallback web search).
      4. Brave Search direct.
      5. DuckDuckGo direct.

    Filters candidate URLs by house number to avoid nearby-listing false matches.
    Returns the full Redfin URL or None if not found.
    """
    import re as _re

    addr = address.strip()
    # "redfin.com" in query gives more specific results than just "redfin"
    query = f"redfin.com {addr}"

    # Extract house number for URL matching (e.g. "937" from "937 N 71st St")
    house_m = _re.match(r"^(\d+)", addr)
    house_num = house_m.group(1) if house_m else ""

    # Extract numeric street identifier (e.g. "71" from "71st St")
    street_m = _re.search(r"\b(\d+)(?:st|nd|rd|th)\b", addr, _re.I)
    street_num = street_m.group(1) if street_m else ""

    _LISTING_SEGS = ("/home/", "/condo/", "/townhouse/", "/other/", "/unit/")
    _REDFIN_BASE = "https://www.redfin.com"

    def _best_match(urls: list[str]) -> str | None:
        candidates = [u for u in urls if any(s in u for s in _LISTING_SEGS)]
        if not candidates:
            return None
        if house_num:
            scored = []
            for u in candidates:
                score = 0
                if f"/{house_num}-" in u or f"/{house_num}/" in u:
                    score += 2
                if street_num and street_num in u:
                    score += 1
                scored.append((score, u))
            scored.sort(key=lambda x: -x[0])
            # Only return a result if it actually matched the house number;
            # never fall back to a random first candidate.
            if scored[0][0] >= 2:
                return scored[0][1]
            return None
        return candidates[0]

    def _autocomplete_url_from_response(data: dict) -> str | None:
        """Extract the best listing URL from a Redfin autocomplete API response."""
        results = (data.get("payload") or {}).get("resultList") or []
        for r in results:
            url = r.get("url", "")
            # type 4 = property/listing; also accept any result with a listing segment
            if any(s in url for s in _LISTING_SEGS):
                full_url = _REDFIN_BASE + url if url.startswith("/") else url
                # Verify it matches the house number if we have one
                if not house_num or f"/{house_num}-" in full_url or f"/{house_num}/" in full_url:
                    logger.debug("Autocomplete hit: %s", full_url)
                    return full_url
        return None

    autocomplete_endpoint = (
        f"{_REDFIN_AUTOCOMPLETE_URL}"
        f"?location={addr.replace(' ', '+')}&v=2&start=0&count=10"
    )

    _BRAVE_URL = f"https://search.brave.com/search?q={query.replace(' ', '+')}"
    _DDG_HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "en-US,en;q=0.9",
    }

    def _extract_redfin_urls(html: str) -> list[str]:
        return list(dict.fromkeys(
            _re.findall(r"https?://(?:www\.)?redfin\.com/[^\s\"'<>&]+", html)
        ))

    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            # Strategy 1: Redfin autocomplete via ScraperAPI (exact match, most reliable)
            if _SCRAPERAPI_KEY:
                try:
                    resp = await client.get(
                        "https://api.scraperapi.com/",
                        params={"api_key": _SCRAPERAPI_KEY, "url": autocomplete_endpoint},
                        timeout=25.0,
                    )
                    if resp.status_code == 200:
                        # Strip Redfin's "{}&&" JSONP prefix if present
                        text = resp.text.lstrip()
                        if text.startswith("{") or "payload" in text:
                            try:
                                import json as _json
                                # Redfin prepends "{}&&" to prevent JSON hijacking
                                clean = _re.sub(r"^\{\}&&", "", text)
                                data = _json.loads(clean)
                                result = _autocomplete_url_from_response(data)
                                if result:
                                    logger.info("Resolved %r → %s (autocomplete/scraperapi)", addr, result)
                                    return result
                            except Exception:
                                pass
                except Exception:
                    pass

            # Strategy 2: Redfin autocomplete direct
            try:
                resp = await client.get(
                    autocomplete_endpoint,
                    headers=_DDG_HEADERS,
                    timeout=12.0,
                )
                if resp.status_code == 200:
                    try:
                        import json as _json
                        clean = _re.sub(r"^\{\}&&", "", resp.text.lstrip())
                        data = _json.loads(clean)
                        result = _autocomplete_url_from_response(data)
                        if result:
                            logger.info("Resolved %r → %s (autocomplete/direct)", addr, result)
                            return result
                    except Exception:
                        pass
            except Exception:
                pass

            # Strategy 3: Brave via ScraperAPI proxy (web search fallback)
            if _SCRAPERAPI_KEY:
                try:
                    resp = await client.get(
                        "https://api.scraperapi.com/",
                        params={"api_key": _SCRAPERAPI_KEY, "url": _BRAVE_URL},
                        timeout=25.0,
                    )
                    if resp.status_code == 200 and len(resp.text) > 5000:
                        result = _best_match(_extract_redfin_urls(resp.text))
                        if result:
                            logger.info("Resolved %r → %s (brave/scraperapi)", addr, result)
                            return result
                except Exception:
                    pass

            # Strategy 4: Brave direct
            try:
                resp = await client.get(
                    "https://search.brave.com/search",
                    params={"q": query},
                    headers=_DDG_HEADERS,
                    timeout=12.0,
                )
                if resp.status_code == 200 and len(resp.text) > 5000:
                    result = _best_match(_extract_redfin_urls(resp.text))
                    if result:
                        logger.info("Resolved %r → %s (brave/direct)", addr, result)
                        return result
            except Exception:
                pass

            # Strategy 5: DuckDuckGo direct
            from urllib.parse import unquote as _unquote
            try:
                resp = await client.get(
                    "https://html.duckduckgo.com/html/",
                    params={"q": f"redfin {addr}"},
                    headers=_DDG_HEADERS,
                    timeout=12.0,
                )
                encoded_urls = _re.findall(r"uddg=([^&\"]+)", resp.text)
                result = _best_match([_unquote(e) for e in encoded_urls])
                if result:
                    logger.info("Resolved %r → %s (ddg)", addr, result)
                    return result
            except Exception:
                pass

    except Exception as e:
        logger.warning("Address resolution failed for %r: %s", addr, e)
    return None


async def _scraperapi_autocomplete(city: str, client: httpx.AsyncClient) -> tuple[str, str]:
    """
    Use ScraperAPI to call Redfin's location-autocomplete endpoint.
    Redfin blocks direct calls (403); ScraperAPI bypasses bot detection.
    Returns (city_id, path) or ('', '') on failure.
    """
    import json as _json
    try:
        autocomplete_url = (
            f"{_REDFIN_AUTOCOMPLETE_URL}"
            f"?location={city.replace(' ', '+')}&v=2"
        )
        resp = await client.get(
            "https://api.scraperapi.com/",
            params={"api_key": _SCRAPERAPI_KEY, "url": autocomplete_url},
            timeout=30.0,
        )
        resp.raise_for_status()
        # Redfin prepends "{}&&" to prevent JSON hijacking — strip it before parsing.
        text = re.sub(r"^\{\}&&", "", resp.text.lstrip())
        data = _json.loads(text)
        payload = data.get("payload") or {}
        # Try both response shapes: legacy resultList and current sections[].rows[]
        candidates: list[dict] = list(payload.get("resultList") or [])
        for section in payload.get("sections") or []:
            candidates.extend(section.get("rows") or [])
        city_result = next(
            (r for r in candidates if str(r.get("id", "")).startswith("city_")),
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
                # Guard: ScraperAPI returns HTML when the Redfin URL redirects
                # (e.g. a stale county ID). resp.json() on HTML → cryptic JSONDecodeError.
                body = resp.text.strip()
                if not body.startswith("{") and not body.startswith("["):
                    raise RuntimeError(
                        f"ScraperAPI returned non-JSON for {search_url}. "
                        "The Redfin URL may be outdated (Redfin changes region IDs). "
                        "Clear scraperapi_search_urls in config.yaml so the pipeline "
                        "auto-resolves fresh URLs from your city list."
                    )
                raw_json = resp.json()
                # Only cache non-empty results — 0 listings is likely a transient
                # ScraperAPI issue (rate/cache artifact) and should not be persisted.
                if raw_json.get("listing"):
                    _cache_set(search_url, raw_json)
                else:
                    logger.warning("ScraperAPI returned 0 listings for %s — skipping cache", search_url)

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


# ── Rentcast backend ───────────────────────────────────────────────────────────

# An upper bound for min-only criteria, because Rentcast documents ranges as
# "min:max" and says nothing about open-ended forms like "3:". No home this
# pipeline would shortlist has 30 bedrooms or bathrooms, so the cap filters
# nothing in practice while keeping to syntax the API documents.
_RENTCAST_RANGE_MAX = 30


def _rentcast_query_params(fetch_config: FetchConfig) -> dict:
    """
    Build the /listings/sale query from our criteria.

    Rentcast's parameter names are not the obvious ones, and it ignores what it
    does not recognise rather than erroring — so a wrong name reads exactly like
    a filter that matched everything. Per the API reference (checked 2026-08-18)
    there is no minPrice/maxPrice at all: price, bedrooms, bathrooms and
    squareFootage are single parameters taking either one value, a "min:max"
    range, or "a|b" alternatives.

    This matters twice over on the free tier's 50 calls/month: an unfiltered
    query pages through every active listing in the city at 500 per call, and a
    bare `bedrooms=3` means *exactly* three — quietly dropping the 4- and 5-bed
    homes that a 3-bed minimum is supposed to include.

    Unverified against a live response: the key on this machine returns 403
    billing/subscription-inactive, so this follows the documentation.
    """
    params: dict = {"status": "Active", "limit": 500}

    lo, hi = fetch_config.rentcast_min_price, fetch_config.rentcast_max_price
    if hi is not None:
        params["price"] = f"{int(lo or 0)}:{int(hi)}"
    # A min with no max is left to local screening rather than guessed at: a
    # documented range needs both ends, and price_too_low is cheap to catch here.

    if fetch_config.rentcast_min_beds is not None:
        params["bedrooms"] = f"{int(fetch_config.rentcast_min_beds)}:{_RENTCAST_RANGE_MAX}"
    if fetch_config.rentcast_min_baths is not None:
        baths = fetch_config.rentcast_min_baths
        lo_baths = int(baths) if baths == int(baths) else baths
        params["bathrooms"] = f"{lo_baths}:{_RENTCAST_RANGE_MAX}"

    if fetch_config.rentcast_home_types:
        mapped = [_HOME_TYPE_TO_RENTCAST.get(t, t) for t in fetch_config.rentcast_home_types]
        # Alternatives are pipe-separated, so several types no longer mean no
        # filter at all — which is what the single-type-only branch used to do.
        params["propertyType"] = "|".join(mapped)

    # daysOld takes a single value as its maximum. max_dom carries 9999 as its
    # "no limit" sentinel; sending that would be a filter matching everything.
    if fetch_config.rentcast_max_dom is not None and 1 <= fetch_config.rentcast_max_dom < 9999:
        params["daysOld"] = int(fetch_config.rentcast_max_dom)

    return params


async def _fetch_from_rentcast(fetch_config: FetchConfig) -> list[RawListing]:
    """
    Fetch active sale listings from the Rentcast API.

    Queries /v1/listings/sale per city (up to 3 cities, auto-populated by pipeline.py
    from criteria.allowed_cities). Responses are cached for SCRAPERAPI_CACHE_TTL_HOURS.

    Requires RENTCAST_API_KEY in .env.
    Free tier: 50 calls/month — sign up at https://app.rentcast.io/app/login
    """
    api_key = _RENTCAST_KEY
    if not api_key:
        raise RuntimeError(
            "RENTCAST_API_KEY is not set. Add it to .env.\n"
            "Sign up free (50 calls/mo): https://app.rentcast.io/app/login"
        )

    cities = fetch_config.rentcast_cities
    state = fetch_config.rentcast_state

    if not cities or not state:
        raise RuntimeError(
            "Rentcast requires city and state. These are auto-populated from "
            "criteria.allowed_cities and the market name — check pipeline.py."
        )

    base_params = _rentcast_query_params(fetch_config)

    all_listings: list[RawListing] = []
    seen: set[str] = set()

    async with httpx.AsyncClient(timeout=30.0) as client:
        for city in cities:
            city_params = {**base_params, "city": city, "state": state}
            cache_key = f"rentcast_{json.dumps(city_params, sort_keys=True)}"

            cached = _cache_get(cache_key)
            if cached is not None:
                logger.info("Rentcast: cache hit for %s, %s (skipping API call)", city, state)
                raw_items = cached
            else:
                raw_items = []
                offset = 0
                while True:
                    params = {**city_params, "offset": offset}
                    logger.info("Rentcast: fetching %s, %s (offset=%d)", city, state, offset)
                    try:
                        resp = await client.get(
                            f"{_RENTCAST_BASE_URL}/listings/sale",
                            headers={"X-Api-Key": api_key, "Accept": "application/json"},
                            params=params,
                            timeout=30.0,
                        )
                        resp.raise_for_status()
                    except httpx.HTTPStatusError as e:
                        code = e.response.status_code
                        if code == 401:
                            raise RuntimeError(
                                "RENTCAST_API_KEY is invalid. Check your .env."
                            ) from e
                        if code == 429:
                            raise RuntimeError(
                                "Rentcast API rate limit exceeded. "
                                "Try again later or upgrade your plan."
                            ) from e
                        if code == 403:
                            # The key is valid but the subscription lapsed. The
                            # generic message below sent you looking for a bad
                            # city name instead of the dashboard.
                            raise RuntimeError(
                                "Rentcast rejected the request: the API key has no active "
                                "subscription. Re-activate the free tier at "
                                "https://app.rentcast.io/app/api — or switch "
                                "fetch.data_source to scraperapi in config.yaml."
                            ) from e
                        raise RuntimeError(
                            f"Rentcast returned HTTP {code} for {city}, {state}"
                        ) from e

                    page = resp.json()
                    if not page:
                        break
                    raw_items.extend(page)
                    logger.info(
                        "Rentcast: +%d listings from %s, %s (offset=%d)",
                        len(page), city, state, offset,
                    )
                    if len(page) < 500:
                        break
                    offset += 500

                _cache_set(cache_key, raw_items)

            before = len(all_listings)
            for item in raw_items:
                listing = _rentcast_to_raw_listing(item)
                if listing:
                    addr_key = listing.address.strip().lower()
                    if addr_key not in seen:
                        seen.add(addr_key)
                        all_listings.append(listing)
            logger.info(
                "Rentcast: +%d unique listings from %s (total=%d)",
                len(all_listings) - before, city, len(all_listings),
            )

    logger.info("Rentcast: %d total unique listings fetched", len(all_listings))
    return all_listings


def _rentcast_to_raw_listing(item: dict) -> RawListing | None:
    """Convert one Rentcast API listing to a RawListing."""
    try:
        address = (item.get("formattedAddress") or "").strip()
        if not address:
            parts = [
                item.get("addressLine1", ""),
                item.get("city", ""),
                item.get("state", ""),
                item.get("zipCode", ""),
            ]
            address = ", ".join(p for p in parts if p).strip(", ")
        if not address:
            return None

        hoa_fee: float | None = None
        hoa_data = item.get("hoa")
        if isinstance(hoa_data, dict):
            fee = hoa_data.get("fee")
            if fee is not None:
                hoa_fee = float(fee) if float(fee) > 0 else None

        dom = item.get("daysOnMarket")
        if dom is None:
            listed = item.get("listedDate") or item.get("lastSeenDate")
            if listed:
                from datetime import date as _date
                try:
                    dom = (_date.today() - _date.fromisoformat(listed[:10])).days
                except Exception:
                    pass

        photos = item.get("photos") or []
        photo_url = photos[0] if photos else None

        return RawListing(
            zpid=item.get("id") or address,
            address=address,
            price=float(item["price"]) if item.get("price") is not None else None,
            beds=int(item["bedrooms"]) if item.get("bedrooms") is not None else None,
            baths=float(item["bathrooms"]) if item.get("bathrooms") is not None else None,
            sqft=int(item["squareFootage"]) if item.get("squareFootage") is not None else None,
            lot_sqft=int(item["lotSize"]) if item.get("lotSize") is not None else None,
            home_type=_RENTCAST_PROP_TYPE_MAP.get((item.get("propertyType") or "").strip()),
            hoa_fee=hoa_fee,
            days_on_market=int(dom) if dom is not None else None,
            latitude=float(item["latitude"]) if item.get("latitude") is not None else None,
            longitude=float(item["longitude"]) if item.get("longitude") is not None else None,
            year_built=int(item["yearBuilt"]) if item.get("yearBuilt") is not None else None,
            photo_url=photo_url,
        )
    except Exception as exc:
        logger.warning("Skipping malformed Rentcast listing: %s", exc)
        return None
