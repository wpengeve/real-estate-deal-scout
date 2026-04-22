"""
ScraperAPI Redfin structured response → RawListing normalizer.

ScraperAPI returns semi-structured JSON where several fields are free-text
strings embedded in lists (key_facts, badge). This module parses those
into typed values for the canonical RawListing schema.

Field mapping:
    address                     → address (used as-is)
    number_beds  "3 beds"       → beds (int)
    number_baths "2.5 baths"    → baths (float)
    sq_ft        "1,450 sq ft"  → sqft (int)
    price[0].cost               → price (float)
    key_facts:
      "X,XXX sq ft lot"         → lot_sqft (int)
      "$XXX HOA"                → hoa_fee (float, monthly)
      "X garage spots"          → (noted; not a RawListing field)
    badge:
      "NEW X HRS AGO"           → days_on_market = 0
      "X DAYS ON REDFIN"        → days_on_market (int)
    url / property_url          → listing_url
"""
import logging
import re

from tools.models import RawListing

logger = logging.getLogger(__name__)


def normalize(raw: dict, index: int = 0) -> RawListing | None:
    """
    Convert one ScraperAPI listing dict to a RawListing.

    Returns None if the record is missing required fields or fails to parse.
    index is used as a fallback zpid.
    """
    try:
        address = (raw.get("address") or "").strip()
        if not address:
            logger.debug("ScraperAPI listing %d: missing address, skipping", index)
            return None

        url = (
            raw.get("url")
            or raw.get("property_url")
            or raw.get("listing_url")
            or ""
        ).strip()

        zpid = url.rstrip("/").split("/")[-1] if url else str(index)

        key_facts = raw.get("key_facts") or []
        badge = raw.get("badge") or []

        return RawListing(
            zpid=zpid,
            address=address,
            price=_parse_price(raw),
            beds=_parse_beds(raw.get("number_beds") or ""),
            baths=_parse_baths(raw.get("number_baths") or ""),
            sqft=_parse_sqft(raw.get("sq_ft") or ""),
            lot_sqft=_parse_lot_sqft(key_facts),
            hoa_fee=_parse_hoa(key_facts),
            days_on_market=_parse_dom(badge),
            listing_url=url or None,
            # lat/lon resolved later via geocoding in fetch.py
        )
    except Exception as exc:
        logger.warning("ScraperAPI listing %d failed to normalize: %s", index, exc)
        return None


def normalize_all(response: dict | list) -> list[RawListing]:
    """
    Normalize a full ScraperAPI search response.

    Accepts either the full response dict ({"listing": [...]}) or a bare list.
    """
    if isinstance(response, list):
        items = response
    else:
        items = response.get("listing") or response.get("listings") or []

    results = []
    for i, item in enumerate(items):
        listing = normalize(item, index=i)
        if listing:
            results.append(listing)
    return results


# ── Field parsers ──────────────────────────────────────────────────────────────

def _parse_price(raw: dict) -> float | None:
    price_list = raw.get("price")
    if isinstance(price_list, list) and price_list:
        cost = price_list[0].get("cost")
        if cost is not None:
            try:
                return float(cost)
            except (ValueError, TypeError):
                pass
    # Fallback: plain string like "$750,000"
    price_str = str(raw.get("price") or "")
    return _parse_number(price_str)


def _parse_beds(s: str) -> int | None:
    """Parse "3 beds" → 3, "Studio" → 0."""
    if not s:
        return None
    if "studio" in s.lower():
        return 0
    m = re.search(r"(\d+)", s)
    return int(m.group(1)) if m else None


def _parse_baths(s: str) -> float | None:
    """Parse "2.5 baths" → 2.5."""
    if not s:
        return None
    m = re.search(r"([\d.]+)", s)
    return float(m.group(1)) if m else None


def _parse_sqft(s: str) -> int | None:
    """Parse "1,450 sq ft" → 1450."""
    return _parse_int_with_commas(s)


def _parse_lot_sqft(key_facts: list[str]) -> int | None:
    """Find "4,160 sq ft lot" in key_facts and return 4160."""
    for fact in key_facts:
        m = re.search(r"([\d,]+)\s*sq\s*ft\s*lot", fact, re.I)
        if m:
            return _parse_int_with_commas(m.group(1))
    return None


def _parse_hoa(key_facts: list[str]) -> float | None:
    """Find "$250 HOA" or "$250/mo HOA" in key_facts and return 250."""
    for fact in key_facts:
        if "hoa" in fact.lower():
            m = re.search(r"\$([\d,]+)", fact)
            if m:
                val = _parse_int_with_commas(m.group(1))
                return float(val) if val is not None else None
    return None


def _parse_dom(badge: list[str]) -> int | None:
    """
    Parse days on market from badge strings.

    "NEW 2 HRS AGO"       → 0
    "NEW 1 DAY AGO"       → 1
    "5 DAYS ON REDFIN"    → 5
    "PRICE DROP"          → None (not a DOM badge)
    """
    for b in badge:
        b_upper = b.upper()
        # "X DAYS ON REDFIN"
        m = re.search(r"(\d+)\s+DAYS?\s+ON\s+REDFIN", b_upper)
        if m:
            return int(m.group(1))
        # "NEW X HRS AGO" or "NEW X HOURS AGO" → same day
        if re.search(r"NEW\s+\d+\s+HR", b_upper):
            return 0
        # "NEW 1 DAY AGO"
        m = re.search(r"NEW\s+(\d+)\s+DAYS?\s+AGO", b_upper)
        if m:
            return int(m.group(1))
        # Plain "NEW" badge with no time — just listed
        if b_upper.strip() == "NEW":
            return 0
    return None


# ── Helpers ────────────────────────────────────────────────────────────────────

def _parse_number(s: str) -> float | None:
    if not s:
        return None
    cleaned = re.sub(r"[^\d.]", "", s)
    try:
        return float(cleaned) if cleaned else None
    except ValueError:
        return None


def _parse_int_with_commas(s: str) -> int | None:
    if not s:
        return None
    cleaned = s.replace(",", "").strip()
    m = re.search(r"\d+", cleaned)
    if m:
        try:
            return int(m.group())
        except ValueError:
            pass
    return None