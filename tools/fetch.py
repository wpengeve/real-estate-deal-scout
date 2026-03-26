"""
Fetch listings from a data source.

Three backends:
  fixtures — load from fixtures/listings.json (default, for development)
  csv      — load from a locally saved Redfin CSV (recommended for real data)
  redfin   — fetch live CSV from Redfin's "Download All" endpoint (no API key)

To use the csv backend:
  1. Go to redfin.com and search your city/filters
  2. Click "Download All" (bottom of results page)
  3. Save the file to data/redfin.csv  (or set fetch.csv_path in config.yaml)
  4. Set fetch.data_source: csv in config.yaml
"""
import csv
import io
import json
import logging
from pathlib import Path

import httpx

from tools.models import FetchConfig, RawListing

logger = logging.getLogger(__name__)

_DEFAULT_FIXTURES = Path(__file__).parent.parent / "fixtures" / "listings.json"

# Redfin's internal CSV download endpoint (same one used by the website's Download All button)
_REDFIN_CSV_URL = "https://www.redfin.com/stingray/api/gis-csv"
_REDFIN_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
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
