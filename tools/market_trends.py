"""
Area-level market trends from the Redfin Data Center city market tracker.

Source (free, no API key, no quota, no rate limit):
    https://redfin-public-data.s3.us-west-2.amazonaws.com/redfin_market_tracker/
    city_market_tracker.tsv000.gz

Why this module is shaped as a batch refresh
--------------------------------------------
Rows in the upstream file are NOT sorted by region, so extracting a single city
means scanning the entire ~950 MB gzip (several GB decompressed). That makes a
per-request fetch impossible. Instead:

    scout.py market-refresh --state WA     # download + filter once, writes a slice
    snapshot_for_address(...)              # cheap local read at render time

Metric caveats (Redfin methodology) — these shape how the numbers may be shown
-----------------------------------------------------------------------------
* sale-to-list and % above list use the FINAL list price, not the original. A
  home listed at $1M, cut to $900k, sold at $900k scores 1.000 and reads as
  "sold at asking". These metrics systematically understate seller weakness, so
  price drops must be displayed alongside as the counterweight.
* sale-to-list / % above list cover homes that CLOSED in the period; median DOM
  covers homes that went UNDER CONTRACT in it. Different cohorts — never narrate
  them as one group.
* median DOM stops at contract, not closing (add ~30-45 days for the close).
* A long tail of small cities sells 1-3 homes a month, which makes percentages
  meaningless ("100% sold above list" off one sale). See MIN_SALES_FOR_RATES.

Attribution: data provided by Redfin. Licensing terms are unresolved as of
2026-08-03 — see TODOS.md before exposing this publicly.
"""
import csv
import gzip
import json
import logging
import os
import urllib.request
from datetime import date
from pathlib import Path

from tools.models import MIN_SALES_FOR_RATES, MarketSnapshot

logger = logging.getLogger(__name__)

__all__ = [
    "MIN_SALES_FOR_RATES", "MarketSnapshot", "refresh", "snapshot_for_city",
    "snapshot_for_address", "history_for_city", "parse_city_state", "slice_path",
]

_SOURCE_URL = (
    "https://redfin-public-data.s3.us-west-2.amazonaws.com/"
    "redfin_market_tracker/city_market_tracker.tsv000.gz"
)
_DOWNLOAD_TIMEOUT = 900.0  # generous: the file is ~950 MB
_HEAD_TIMEOUT = 30.0       # headers only — used to skip needless downloads

_DATA_DIR = Path("data")

# Only these are kept from the upstream file's 58 columns.
_KEPT_COLUMNS = [
    "PERIOD_BEGIN", "PERIOD_END", "REGION_TYPE", "CITY", "STATE_CODE",
    "PROPERTY_TYPE", "MEDIAN_SALE_PRICE", "MEDIAN_LIST_PRICE", "MEDIAN_PPSF",
    "HOMES_SOLD", "NEW_LISTINGS", "PENDING_SALES", "INVENTORY",
    "MONTHS_OF_SUPPLY", "MEDIAN_DOM", "AVG_SALE_TO_LIST", "SOLD_ABOVE_LIST",
    "PRICE_DROPS", "OFF_MARKET_IN_TWO_WEEKS", "MEDIAN_SALE_PRICE_YOY",
    "MEDIAN_DOM_YOY", "AVG_SALE_TO_LIST_YOY", "INVENTORY_YOY",
]

DEFAULT_PROPERTY_TYPE = "All Residential"

# Keep this many years of history in the local slice (enough for trend lines).
_HISTORY_YEARS = 3

# slice path -> (fingerprint, city_key -> rows newest-period-first).
# Keyed by path so a run spanning two states does not re-parse on every lookup,
# and fingerprinted on (mtime, size) so a long-lived process (the FastAPI app)
# picks up a slice refreshed by a separate `scout.py --market-refresh` run
# instead of serving its first-seen copy until restart.
_index_cache: dict[Path, tuple[tuple[int, int] | None, dict[str, list[dict]]]] = {}


# ── Parsing helpers ───────────────────────────────────────────────────────────

def _clean(value: str | None) -> str:
    """Strip the upstream file's surrounding double quotes."""
    if value is None:
        return ""
    return value.strip().strip('"').strip()


def _num(value: str | None) -> float | None:
    """
    Parse a numeric cell, treating the file's "NA" and empty cells as missing.

    `NA` is a real value here — 19 WA rows had NA median DOM in May 2026 alone —
    and must never be coerced to 0.
    """
    text = _clean(value)
    if not text or text.upper() == "NA":
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _city_key(city: str, state: str) -> str:
    return f"{city.strip().lower()}|{state.strip().upper()}"


def parse_city_state(address: str | None) -> tuple[str, str] | None:
    """
    Pull (city, state) out of an address like
    "4521 Rainier Ave S, Seattle, WA 98118" -> ("Seattle", "WA").

    Returns None when the address doesn't carry a parseable city and state.
    """
    if not address:
        return None

    # Empty segments are kept rather than filtered: dropping them would let
    # "123 Main St, , WA 98118" slide the street name into the city position.
    parts = [p.strip() for p in address.split(",")]
    while parts and not parts[-1]:
        parts.pop()
    if len(parts) < 2:
        return None

    # Last part is "WA 98118" (or just "WA"); the one before it is the city.
    tail = parts[-1].split()
    if not tail:
        return None
    state = tail[0].upper()
    if len(state) != 2 or not state.isalpha():
        return None

    city = parts[-2].strip()
    if not city:
        return None
    return city, state


# ── Refresh (batch) ───────────────────────────────────────────────────────────

def _default_data_dir() -> Path:
    """
    Where slices live when the caller doesn't say.

    MARKET_TRENDS_DIR lets a deployment keep the slice somewhere other than the
    repo's data/ — a mounted volume, or a read-only path baked into an image —
    without the refresh and the app disagreeing about where it is. Read lazily
    so load_dotenv() timing doesn't matter.
    """
    override = os.getenv("MARKET_TRENDS_DIR")
    return Path(override) if override else _DATA_DIR


def slice_path(state: str, data_dir: Path | None = None) -> Path:
    """Local path of the filtered slice for a state."""
    base = data_dir if data_dir is not None else _default_data_dir()
    return base / f"market_trends_{state.upper()}.tsv"


def _meta_path(state: str, data_dir: Path | None = None) -> Path:
    return slice_path(state, data_dir).with_suffix(".meta.json")


def _read_meta(state: str, data_dir: Path | None = None) -> dict:
    try:
        return json.loads(_meta_path(state, data_dir).read_text(encoding="utf-8"))
    except Exception:
        return {}


def local_last_modified(state: str, data_dir: Path | None = None) -> str | None:
    """Upstream Last-Modified that the existing local slice was built from."""
    if not slice_path(state, data_dir).exists():
        return None
    return _read_meta(state, data_dir).get("last_modified")


def upstream_last_modified(source_url: str = _SOURCE_URL) -> str | None:
    """
    Last-Modified of the upstream file, via a HEAD request. None if unavailable.

    Cheap (headers only) — this is what makes conditional refresh possible.
    """
    try:
        request = urllib.request.Request(source_url, method="HEAD")
        with urllib.request.urlopen(request, timeout=_HEAD_TIMEOUT) as response:
            return response.headers.get("Last-Modified")
    except Exception as e:
        logger.warning("Could not HEAD %s: %r", source_url, e)
        return None


def refresh(
    state: str = "WA",
    data_dir: Path | None = None,
    source_url: str = _SOURCE_URL,
    history_years: int = _HISTORY_YEARS,
    force: bool = False,
) -> Path:
    """
    Download the upstream file, filter it to one state, and write a local slice.

    Skips the download entirely when the upstream Last-Modified matches what the
    existing slice was built from (pass force=True to override). This matters:
    Redfin's monthly tracker files do not publish on a dependable schedule — all
    five were stamped 2026-06-02 within four minutes of each other and had not
    moved two months later — so a blind monthly job would re-download ~950 MB to
    produce a byte-identical slice. Checking headers first costs one request.

    Streams and discards as it goes, so peak memory stays flat regardless of the
    download size. Writes to a temporary file and renames only on success, so an
    interrupted refresh leaves the previous slice intact rather than a truncated
    one.

    Returns the path of the current slice. Raises on network/parse failure — the
    CLI reports it; lookups continue to serve the previous slice.
    """
    state = state.upper()
    dest = slice_path(state, data_dir)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".tsv.partial")

    cutoff = f"{date.today().year - history_years}-01-01"
    remote_stamp = upstream_last_modified(source_url)

    # Skip the 950MB download only when the existing slice is genuinely current
    # *and* usable. Matching the upstream stamp alone is not enough: a truncated
    # or zero-row slice would match forever and silently serve no market data,
    # and a caller asking for a different history window or source URL would be
    # handed the old slice while believing it got what it asked for.
    if not force and dest.exists() and remote_stamp:
        meta = _read_meta(state, data_dir)
        if (
            meta.get("last_modified") == remote_stamp
            and meta.get("cutoff") == cutoff
            and meta.get("source_url") == source_url
            and (meta.get("rows") or 0) > 0
            and dest.stat().st_size > 0
        ):
            logger.info(
                "Upstream unchanged since %s — keeping existing slice %s",
                remote_stamp, dest,
            )
            return dest

    logger.info("Refreshing market trends for %s from %s", state, source_url)

    kept = 0
    scanned = 0
    try:
        request = urllib.request.Request(
            source_url, headers={"Accept-Encoding": "identity"}
        )
        with urllib.request.urlopen(request, timeout=_DOWNLOAD_TIMEOUT) as response:
            with gzip.open(response, mode="rt", encoding="utf-8", errors="replace") as raw:
                reader = csv.DictReader(raw, delimiter="\t")
                with tmp.open("w", encoding="utf-8", newline="") as out:
                    writer = csv.DictWriter(
                        out, fieldnames=_KEPT_COLUMNS, delimiter="\t",
                        extrasaction="ignore",
                    )
                    writer.writeheader()
                    for row in reader:
                        scanned += 1
                        if _clean(row.get("STATE_CODE")) != state:
                            continue
                        if _clean(row.get("PERIOD_BEGIN")) < cutoff:
                            continue
                        writer.writerow(
                            {col: _clean(row.get(col)) for col in _KEPT_COLUMNS}
                        )
                        kept += 1
    except Exception:
        tmp.unlink(missing_ok=True)
        raise

    tmp.replace(dest)
    _meta_path(state, data_dir).write_text(
        json.dumps({
            "last_modified": remote_stamp,
            "source_url": source_url,
            "cutoff": cutoff,
            "rows": kept,
        }, indent=2),
        encoding="utf-8",
    )
    logger.info(
        "Market trends refreshed: %d rows kept for %s (scanned %d) -> %s",
        kept, state, scanned, dest,
    )
    _invalidate_cache()
    return dest


# ── Lookup ────────────────────────────────────────────────────────────────────

def _invalidate_cache() -> None:
    _index_cache.clear()


def _row_to_snapshot(row: dict) -> MarketSnapshot:
    homes_sold = _num(row.get("HOMES_SOLD"))
    return MarketSnapshot(
        city=_clean(row.get("CITY")),
        state=_clean(row.get("STATE_CODE")),
        period_end=_clean(row.get("PERIOD_END")),
        property_type=_clean(row.get("PROPERTY_TYPE")),
        homes_sold=int(homes_sold) if homes_sold is not None else None,
        median_sale_price=_num(row.get("MEDIAN_SALE_PRICE")),
        median_list_price=_num(row.get("MEDIAN_LIST_PRICE")),
        median_ppsf=_num(row.get("MEDIAN_PPSF")),
        inventory=_num(row.get("INVENTORY")),
        new_listings=_num(row.get("NEW_LISTINGS")),
        pending_sales=_num(row.get("PENDING_SALES")),
        months_of_supply=_num(row.get("MONTHS_OF_SUPPLY")),
        median_dom=_num(row.get("MEDIAN_DOM")),
        sale_to_list=_num(row.get("AVG_SALE_TO_LIST")),
        pct_above_list=_num(row.get("SOLD_ABOVE_LIST")),
        pct_price_drops=_num(row.get("PRICE_DROPS")),
        pct_off_market_two_weeks=_num(row.get("OFF_MARKET_IN_TWO_WEEKS")),
        median_sale_price_yoy=_num(row.get("MEDIAN_SALE_PRICE_YOY")),
        median_dom_yoy=_num(row.get("MEDIAN_DOM_YOY")),
        sale_to_list_yoy=_num(row.get("AVG_SALE_TO_LIST_YOY")),
        inventory_yoy=_num(row.get("INVENTORY_YOY")),
    )


def _slice_fingerprint(path: Path) -> tuple[int, int] | None:
    """(mtime_ns, size) for a slice, or None when it is missing/unreadable."""
    try:
        stat = path.stat()
    except OSError:
        return None
    return (stat.st_mtime_ns, stat.st_size)


def _load_index(state: str, data_dir: Path | None = None) -> dict[str, list[dict]]:
    """
    Load the local slice into memory, newest period first per city.

    Returns an empty index (never raises) when the slice is missing or
    unreadable — a missing refresh degrades to "no market context", not a crash.
    """
    path = slice_path(state, data_dir)
    fingerprint = _slice_fingerprint(path)

    cached = _index_cache.get(path)
    if cached is not None and cached[0] == fingerprint:
        return cached[1]

    index: dict[str, list[dict]] = {}
    if fingerprint is None:
        logger.info(
            "No market-trends slice at %s — run `scout.py market-refresh --state %s`",
            path, state,
        )
        _index_cache[path] = (fingerprint, index)
        return index

    try:
        with path.open(encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle, delimiter="\t"):
                city = _clean(row.get("CITY"))
                state_code = _clean(row.get("STATE_CODE"))
                if not city or not state_code:
                    continue
                index.setdefault(_city_key(city, state_code), []).append(row)
    except Exception as e:
        logger.warning("Could not read market-trends slice %s: %r", path, e)
        _index_cache[path] = (fingerprint, {})
        return {}

    for rows in index.values():
        rows.sort(key=lambda r: _clean(r.get("PERIOD_END")), reverse=True)

    _index_cache[path] = (fingerprint, index)
    return index


def snapshot_for_city(
    city: str,
    state: str,
    property_type: str = DEFAULT_PROPERTY_TYPE,
    data_dir: Path | None = None,
) -> MarketSnapshot | None:
    """
    Latest available snapshot for a city, or None if we have no data for it.

    Matching is exact (case-insensitive) on city + state. That matters: the
    upstream REGION_TYPE is `place`, which includes Seattle neighbourhoods
    ("Beacon Hill", "Bryant", "Cascade Valley") as their own rows alongside
    "Seattle". Exact matching keeps a Seattle address on the Seattle row.
    """
    rows = _load_index(state, data_dir).get(_city_key(city, state))
    if not rows:
        return None

    for row in rows:  # already newest-first
        if _clean(row.get("PROPERTY_TYPE")) == property_type:
            return _row_to_snapshot(row)
    return None


def snapshot_for_address(
    address: str | None,
    property_type: str = DEFAULT_PROPERTY_TYPE,
    data_dir: Path | None = None,
) -> MarketSnapshot | None:
    """Convenience wrapper: parse city/state out of an address, then look up."""
    parsed = parse_city_state(address)
    if parsed is None:
        return None
    city, state = parsed
    return snapshot_for_city(city, state, property_type, data_dir)


def history_for_city(
    city: str,
    state: str,
    property_type: str = DEFAULT_PROPERTY_TYPE,
    months: int = 24,
    data_dir: Path | None = None,
) -> list[MarketSnapshot]:
    """Most recent `months` snapshots, newest first. Empty when unknown."""
    rows = _load_index(state, data_dir).get(_city_key(city, state), [])
    matching = [r for r in rows if _clean(r.get("PROPERTY_TYPE")) == property_type]
    return [_row_to_snapshot(r) for r in matching[:months]]