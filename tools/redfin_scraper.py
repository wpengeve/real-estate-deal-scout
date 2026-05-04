"""
Redfin listing page scraper — extracts structured features from the HTML.

Parses the <li class="entryItem"> feature sections that Redfin renders
server-side, the embedded walkScoreInfo JSON blob, and the marketing remarks.

Fields extracted:
    walk_score          int  | None  — Walk Score (0–100)
    bike_score          int  | None  — Bike Score (0–100)
    transit_score       int  | None  — Transit Score (0–100)
    year_built          int  | None  — year property was built (from schema.org JSON-LD)
    has_primary_suite   bool | None  — listing contains a "Primary Bedroom" room type
    has_garage          bool | None  — attached or detached garage present
    garage_spaces       int  | None  — number of covered/garage spaces
    has_basement        bool | None  — basement features section present
    basement_finished   bool | None  — basement described as finished/partially finished
    has_fireplace       bool | None
    site_features       list[str]    — e.g. ["Fenced-Partially", "Patio", "Deck"]
    lot_features        list[str]    — e.g. ["Corner Lot", "Curbs"]
    remarks             str  | None  — full listing description text

All fields default to None — scrape failures degrade gracefully.
"""
import logging
import re

import httpx

logger = logging.getLogger(__name__)

_TIMEOUT = 10.0
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-US,en;q=0.9",
}


class ListingFeatures:
    """Structured features extracted from a Redfin listing page."""

    __slots__ = (
        "walk_score",
        "bike_score",
        "transit_score",
        "year_built",
        "has_primary_suite",
        "has_garage",
        "garage_spaces",
        "has_basement",
        "basement_finished",
        "has_fireplace",
        "site_features",
        "lot_features",
        "remarks",
        "photo_url",
    )

    def __init__(self) -> None:
        self.walk_score: int | None = None
        self.bike_score: int | None = None
        self.transit_score: int | None = None
        self.year_built: int | None = None
        self.has_primary_suite: bool | None = None
        self.has_garage: bool | None = None
        self.garage_spaces: int | None = None
        self.has_basement: bool | None = None
        self.basement_finished: bool | None = None
        self.has_fireplace: bool | None = None
        self.site_features: list[str] = []
        self.lot_features: list[str] = []
        self.remarks: str | None = None
        self.photo_url: str | None = None

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in self.__slots__}


def _strip_tags(html: str) -> str:
    """Remove HTML tags and decode basic entities."""
    text = re.sub(r"<[^>]+>", "", html)
    text = text.replace("&amp;", "&").replace("&rsquo;", "'").replace("&mdash;", "—")
    text = text.replace("&nbsp;", " ").replace("&lt;", "<").replace("&gt;", ">")
    return re.sub(r"\s+", " ", text).strip()


def _entry_items(html: str) -> list[str]:
    """Return text of all <li class="entryItem ..."> elements."""
    return [
        _strip_tags(m.group(1))
        for m in re.finditer(r'<li class="entryItem[^"]*">(.*?)</li>', html, re.S)
    ]


def _parse_score(html: str, score_type: str) -> int | None:
    """Extract Walk/Bike/Transit score from Redfin's embedded walkScoreInfo blob."""
    m = re.search(
        rf'{score_type}.{{0,30}}?value.{{0,10}}?([\d]+\.?[\d]*)',
        html,
    )
    if m:
        try:
            return int(float(m.group(1)))
        except ValueError:
            pass
    return None


def parse(html: str) -> ListingFeatures:
    """Parse a Redfin listing HTML page into a ListingFeatures object."""
    f = ListingFeatures()

    # ── Primary listing photo (og:image meta tag) ─────────────────────────────
    m = re.search(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']', html)
    if not m:
        m = re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']', html)
    if m:
        f.photo_url = m.group(1)

    # ── Walk / Bike / Transit scores (embedded in walkScoreInfo JSON blob) ─────
    if "walkScoreInfo" in html:
        idx = html.find("walkScoreInfo")
        chunk = html[idx:idx + 1500]
        f.walk_score = _parse_score(chunk, "walkScore")
        f.bike_score = _parse_score(chunk, "bikeScore")
        f.transit_score = _parse_score(chunk, "transitScore")

    # ── Year built (schema.org JSON-LD embedded in the page) ──────────────────
    m = re.search(r'"yearBuilt"\s*:\s*(\d{4})', html)
    if m:
        try:
            f.year_built = int(m.group(1))
        except ValueError:
            pass

    # ── Primary suite ──────────────────────────────────────────────────────────
    # Room type entries look like: "Room Type: Primary Bedroom"
    if re.search(r"Room Type:\s*Primary Bed", html, re.I):
        f.has_primary_suite = True
    elif "Bathroom Information" in html or "Room 1 Information" in html:
        # Features section present but no primary bedroom mentioned
        f.has_primary_suite = False

    # ── Garage ─────────────────────────────────────────────────────────────────
    if "Has Garage" in html or "Has Attached Garage" in html:
        f.has_garage = True
    elif "Garage Information" in html or "Parking Information" in html:
        f.has_garage = False

    # Covered spaces count — appears in both JSON blob and rendered HTML
    m = re.search(r"Covered Spaces[^\"]*\"amenityValues\":\[\"(\d+)\"", html)
    if not m:
        m = re.search(r"Covered Spaces:\s*(\d+)", html)
    if m:
        try:
            f.garage_spaces = int(m.group(1))
        except ValueError:
            pass

    # ── Basement ───────────────────────────────────────────────────────────────
    if "Basement Information" in html:
        f.has_basement = True
        basement_section = html[html.find("Basement Information"):]
        basement_section = basement_section[:500]
        if re.search(r"Partially Finished|Finished", basement_section, re.I) and \
                not re.search(r"Unfinished", basement_section, re.I):
            f.basement_finished = True
        elif re.search(r"Unfinished", basement_section, re.I):
            f.basement_finished = False

    # ── Fireplace ──────────────────────────────────────────────────────────────
    if "Has Fireplace" in html:
        f.has_fireplace = True
    elif "Fireplace Information" in html:
        f.has_fireplace = False

    # ── Site features (yard, deck, patio, fencing…) ───────────────────────────
    m = re.search(r"Site Features[^:]*:\s*([^<]+)</li>", html)
    if m:
        f.site_features = [s.strip() for s in m.group(1).split(",") if s.strip()]

    # ── Lot features ──────────────────────────────────────────────────────────
    m = re.search(r"Lot Features[^:]*:\s*([^<]+)</li>", html)
    if m:
        f.lot_features = [s.strip() for s in m.group(1).split(",") if s.strip()]

    # ── Listing description (remarks) ─────────────────────────────────────────
    m = re.search(
        r'data-rf-test-id="listingRemarks"[^>]*>(.*?)</div>',
        html,
        re.S,
    )
    if m:
        f.remarks = _strip_tags(m.group(1))[:2000]  # cap at 2000 chars

    return f


async def fetch_listing_features(
    url: str,
    client: httpx.AsyncClient,
) -> ListingFeatures | None:
    """
    Fetch and parse a Redfin listing page.
    Returns None on network error or non-200 response (degrades gracefully).
    """
    if not url or not url.startswith("https://www.redfin.com/"):
        return None
    try:
        resp = await client.get(url, headers=_HEADERS, timeout=_TIMEOUT, follow_redirects=True)
        if resp.status_code != 200:
            logger.debug("Redfin scrape %s → HTTP %s", url, resp.status_code)
            return None
        return parse(resp.text)
    except Exception as e:
        logger.debug("Redfin scrape %s failed: %s", url, e)
        return None