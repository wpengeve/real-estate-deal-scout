"""
Fetch and parse a single Redfin listing URL into a RawListing.

Used by the "analyze a specific property" flow — bypasses market search,
goes straight to enrichment + financial analysis for one property.

Entry point: fetch_single_listing(url) -> RawListing | None
"""
from __future__ import annotations

import logging
import os
import re

import httpx

from tools.models import RawListing
from tools.redfin_scraper import ListingFeatures, parse as parse_features, _HEADERS, _TIMEOUT

logger = logging.getLogger(__name__)

_SCRAPERAPI_KEY = os.getenv("SCRAPERAPI_KEY")


async def fetch_single_listing(url: str) -> tuple[RawListing, ListingFeatures] | None:
    """
    Fetch a Redfin listing page and return (RawListing, ListingFeatures).

    Tries direct fetch first; falls back to ScraperAPI proxy if blocked (403/429).
    Returns None on failure.
    """
    if not url or "redfin.com" not in url:
        return None

    html = await _fetch_html(url)
    if not html:
        return None

    features = parse_features(html)
    listing = _parse_raw_listing(html, url, features)
    if listing is None:
        return None

    return listing, features


async def _fetch_html(url: str) -> str | None:
    """Fetch listing page HTML, using ScraperAPI proxy if direct fetch is blocked."""
    async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
        # Try direct first (free)
        try:
            resp = await client.get(url, headers=_HEADERS)
            if resp.status_code == 200 and len(resp.text) > 5000:
                logger.debug("Direct fetch succeeded for %s", url)
                return resp.text
            logger.debug("Direct fetch returned %s for %s", resp.status_code, url)
        except Exception as e:
            logger.debug("Direct fetch failed for %s: %s", url, e)

        # Fallback: ScraperAPI proxy
        if _SCRAPERAPI_KEY:
            try:
                resp = await client.get(
                    "https://api.scraperapi.com/",
                    params={"api_key": _SCRAPERAPI_KEY, "url": url},
                    timeout=60.0,
                )
                if resp.status_code == 200:
                    logger.debug("ScraperAPI fallback succeeded for %s", url)
                    return resp.text
            except Exception as e:
                logger.debug("ScraperAPI fallback failed for %s: %s", url, e)

    logger.warning("Could not fetch listing page: %s", url)
    return None


def _parse_raw_listing(html: str, url: str, features: ListingFeatures) -> RawListing | None:
    """
    Extract RawListing fields from a Redfin listing page HTML.

    Uses embedded JSON data patterns that Redfin includes server-side.
    Returns None if price cannot be determined (required field).
    """
    # ── og:description — reliable fallback for beds/baths/sqft/price ──────────
    # Redfin og:description is always: "3 bed, 2 bath, 1,400 sq ft house..."
    og_desc = ""
    m = re.search(r'<meta[^>]+property=["\']og:description["\'][^>]+content=["\']([^"\']+)["\']', html)
    if not m:
        m = re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:description["\']', html)
    if m:
        og_desc = m.group(1)

    # ── Price ──────────────────────────────────────────────────────────────────
    price = None
    for pat in [
        r'"listingPrice"\s*:\s*\{\s*"amount"\s*:\s*(\d+)',
        r'"price"\s*:\s*(\d{5,9})',       # 5–9 digits avoids matching zip codes
        r'"initialListingPrice"\s*:\s*\{\s*"amount"\s*:\s*(\d+)',
        r'"listPrice"\s*:\s*(\d+)',
        r'\$(\d[\d,]+)\s*(?:home|house|bed|listing)',  # "$850,000 home"
    ]:
        m = re.search(pat, html, re.I)
        if m:
            try:
                price = float(m.group(1).replace(",", ""))
                if price > 10000:  # sanity check
                    break
                price = None
            except ValueError:
                pass
    # og:description sometimes has price: "Listed for $850,000"
    if not price and og_desc:
        m = re.search(r'\$(\d[\d,]+)', og_desc)
        if m:
            try:
                price = float(m.group(1).replace(",", ""))
            except ValueError:
                pass

    if not price:
        logger.warning("Could not extract price from %s", url)
        return None

    # ── Beds ───────────────────────────────────────────────────────────────────
    beds = None
    for pat in [
        r'"beds"\s*:\s*(\d+)',
        r'"numBeds"\s*:\s*(\d+)',
        r'"bedrooms"\s*:\s*(\d+)',
        r'"numberOfBedrooms"\s*:\s*(\d+)',
        r'(\d+)\s*(?:bed|bedroom)s?',  # "3 beds"
    ]:
        m = re.search(pat, html, re.I)
        if m:
            try:
                beds = int(m.group(1))
                if 1 <= beds <= 20:
                    break
                beds = None
            except ValueError:
                pass
    # og:description fallback: "3 bed" or "3 beds"
    if beds is None and og_desc:
        m = re.search(r'(\d+)\s*bed', og_desc, re.I)
        if m:
            try:
                beds = int(m.group(1))
            except ValueError:
                pass

    # ── Baths ──────────────────────────────────────────────────────────────────
    baths = None
    for pat in [
        r'"numBaths"\s*:\s*([\d.]+)',
        r'"baths"\s*:\s*([\d.]+)',
        r'"bathrooms"\s*:\s*([\d.]+)',
        r'"numBathsFull"\s*:\s*(\d+)',
        r'([\d.]+)\s*(?:bath|bathroom)s?',
    ]:
        m = re.search(pat, html, re.I)
        if m:
            try:
                baths = float(m.group(1))
                if 0.5 <= baths <= 20:
                    break
                baths = None
            except ValueError:
                pass
    if baths is None and og_desc:
        m = re.search(r'([\d.]+)\s*bath', og_desc, re.I)
        if m:
            try:
                baths = float(m.group(1))
            except ValueError:
                pass

    # ── Sqft ───────────────────────────────────────────────────────────────────
    sqft = None
    for pat in [
        r'"sqFt"\s*:\s*\{\s*"value"\s*:\s*([\d.]+)',
        r'"sqft"\s*:\s*(\d+)',
        r'"livingArea"\s*:\s*([\d.]+)',
        r'"floorSizeValue"\s*:\s*([\d.]+)',
        r'([\d,]+)\s*sq\.?\s*ft',
    ]:
        m = re.search(pat, html, re.I)
        if m:
            try:
                sqft = int(float(m.group(1).replace(",", "")))
                if 100 <= sqft <= 30000:
                    break
                sqft = None
            except ValueError:
                pass
    if sqft is None and og_desc:
        m = re.search(r'([\d,]+)\s*sq\.?\s*ft', og_desc, re.I)
        if m:
            try:
                sqft = int(m.group(1).replace(",", ""))
            except ValueError:
                pass

    # ── Lot sqft ───────────────────────────────────────────────────────────────
    lot_sqft = None
    for pat in [
        r'"lotSize"\s*:\s*\{\s*"value"\s*:\s*([\d.]+)',
        r'"lotSqFt"\s*:\s*([\d.]+)',
        r'"lotSizeValue"\s*:\s*([\d.]+)',
    ]:
        m = re.search(pat, html)
        if m:
            try:
                lot_sqft = int(float(m.group(1)))
                break
            except ValueError:
                pass

    # ── Address ────────────────────────────────────────────────────────────────
    address = _extract_address(html, url)

    # ── Lat / Lon ──────────────────────────────────────────────────────────────
    lat, lon = None, None
    m = re.search(r'"latitude"\s*:\s*([-\d.]+)', html)
    if m:
        try:
            lat = float(m.group(1))
        except ValueError:
            pass
    m = re.search(r'"longitude"\s*:\s*([-\d.]+)', html)
    if m:
        try:
            lon = float(m.group(1))
        except ValueError:
            pass

    # ── Home type ──────────────────────────────────────────────────────────────
    home_type = None
    type_map = {
        "SingleFamily": "Single Family",
        "Single Family": "Single Family",
        "Condo": "Condo",
        "Townhouse": "Townhouse",
        "Townhome": "Townhouse",
        "MultiFamily": "Multi-Family",
    }
    for pat in [r'"propertyType"\s*:\s*"([^"]+)"', r'"homeType"\s*:\s*"([^"]+)"']:
        m = re.search(pat, html)
        if m:
            raw_type = m.group(1)
            for key, mapped in type_map.items():
                if key.lower() in raw_type.lower():
                    home_type = mapped
                    break
            break

    # ── HOA fee ────────────────────────────────────────────────────────────────
    hoa_fee = None
    for pat in [
        r'"hoaDues"\s*:\s*([\d.]+)',
        r'"hoa(?:Fee|Dues|Amount)"\s*:\s*([\d.]+)',
        r'HOA Dues[^$]*\$([\d,]+)',
    ]:
        m = re.search(pat, html, re.I)
        if m:
            try:
                hoa_fee = float(m.group(1).replace(",", ""))
                break
            except ValueError:
                pass

    # ── Days on market ─────────────────────────────────────────────────────────
    dom = None
    for pat in [r'"daysOnMarket"\s*:\s*(\d+)', r'"dom"\s*:\s*(\d+)']:
        m = re.search(pat, html)
        if m:
            try:
                dom = int(m.group(1))
                break
            except ValueError:
                pass

    # ── ZIP code ───────────────────────────────────────────────────────────────
    zpid = None
    m = re.search(r'/(\d{7,10})(?:[/?#]|$)', url)  # Redfin listing IDs are 7-10 digits
    if m:
        zpid = m.group(1)
    if not zpid:
        m = re.search(r'"listingId"\s*:\s*(\d+)', html)
        if m:
            zpid = m.group(1)
    zpid = zpid or "single"

    # ── Property tax ───────────────────────────────────────────────────────────
    tax_annual = None
    for pat in [
        r'"taxesDue"\s*:\s*([\d.]+)',
        r'"annualTax(?:es)?"\s*:\s*([\d.]+)',
        r'Annual Tax[^$]*\$([\d,]+)',
    ]:
        m = re.search(pat, html, re.I)
        if m:
            try:
                tax_annual = float(m.group(1).replace(",", ""))
                break
            except ValueError:
                pass

    return RawListing(
        zpid=zpid,
        address=address,
        price=price,
        beds=beds,
        baths=baths,
        sqft=sqft,
        lot_sqft=lot_sqft,
        home_type=home_type,
        hoa_fee=hoa_fee,
        days_on_market=dom,
        year_built=features.year_built,
        latitude=lat,
        longitude=lon,
        listing_url=url,
        photo_url=features.photo_url,
        property_tax_annual=tax_annual,
    )


def _extract_address(html: str, url: str) -> str:
    """Extract the property address from the page, falling back to URL parsing."""
    # Try JSON-LD streetAddress
    m = re.search(r'"streetAddress"\s*:\s*"([^"]+)"', html)
    if m:
        street = m.group(1)
        city = ""
        region = ""
        postal = ""
        mc = re.search(r'"addressLocality"\s*:\s*"([^"]+)"', html)
        if mc:
            city = mc.group(1)
        mr = re.search(r'"addressRegion"\s*:\s*"([^"]+)"', html)
        if mr:
            region = mr.group(1)
        mp = re.search(r'"postalCode"\s*:\s*"([^"]+)"', html)
        if mp:
            postal = mp.group(1)
        parts = [p for p in [street, city, region, postal] if p]
        if parts:
            return ", ".join(parts)

    # Try og:title — usually "123 Main St, Seattle, WA 98101 | Redfin"
    m = re.search(r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\'|]+)', html)
    if not m:
        m = re.search(r'<meta[^>]+content=["\']([^"\'|]+)[^>]+property=["\']og:title["\']', html)
    if m:
        title = m.group(1).split("|")[0].strip()
        if title:
            return title

    # Fall back to URL — e.g. /WA/Seattle/123-Main-St-98101/home/12345678
    m = re.search(r'/([A-Z]{2}/[^/]+/[^/]+-\d{5})/(?:home|condo|townhouse)', url)
    if m:
        return m.group(1).replace("/", ", ").replace("-", " ")

    return url