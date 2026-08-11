"""
HTML report generator for the deal shortlist.

Produces a self-contained HTML file with:
- Interactive Leaflet.js map per property (OpenStreetMap, no API key)
- "View on Redfin" links
- Color-coded financial metrics
- Responsive grid layout

Usage:
    python -m tools.report outputs/20260324-073231_seattle_wa.json
    python -m tools.report outputs/FILE.json --output my_report.html
"""
from __future__ import annotations

import argparse
import json
import sys
from html import escape
from pathlib import Path

from urllib.parse import quote

from tools import market_trends
from tools.models import DealNarrative, FinancialAssumptions, Shortlist
from tools.solar import solar_score

_RISK_COLOR = {
    "LOW":    {"bg": "#f0f9ff", "text": "#0369a1", "border": "#7dd3fc"},
    "MEDIUM": {"bg": "#fff7ed", "text": "#9a3412", "border": "#fdba74"},
    "HIGH":   {"bg": "#fdf2f8", "text": "#831843", "border": "#f0abfc"},
}

_RISK_LABEL = {
    "LOW":    "✓ No Flags",
    "MEDIUM": "⚠ Slow to Sell",
    "HIGH":   "🌊 Flood Zone",
}

_VERDICT_COLOR = {
    "strong buy":           {"bg": "#dcfce7", "text": "#14532d", "border": "#86efac"},
    "buy":                  {"bg": "#d1fae5", "text": "#065f46", "border": "#6ee7b7"},
    "consider":             {"bg": "#dbeafe", "text": "#1e3a8a", "border": "#93c5fd"},
    "proceed with caution": {"bg": "#fef3c7", "text": "#78350f", "border": "#fcd34d"},
    "pass":                 {"bg": "#fee2e2", "text": "#7f1d1d", "border": "#fca5a5"},
}

def _esc(value) -> str:
    """
    Escape a value for an HTML text or attribute context.

    Listing fields (address, zoning, home type, URLs) come from the Redfin feed
    and narratives come from the model; neither is trusted markup. Everything
    interpolated into the page that is not a number or one of our own literals
    goes through here.
    """
    return escape(str(value), quote=True) if value is not None else ""


def _json_for_script(payload) -> str:
    """
    Serialise data for embedding inside a <script> block.

    json.dumps alone is not enough: a "</script>" inside any string value ends
    the block early and drops the rest of the payload into the document as
    markup. Escaping "<" as \\u003c is inert in JSON and removes the only
    sequence that can break out.
    """
    return json.dumps(payload).replace("<", "\\u003c")


def _verdict_style(narrative: str) -> dict:
    """Pick a color style based on the verdict keyword in the narrative."""
    low = narrative.lower()
    for key, style in _VERDICT_COLOR.items():
        if key in low:
            return style
    return {"bg": "#f3f4f6", "text": "#374151", "border": "#d1d5db"}

def _verdict_label(narrative: str) -> str:
    """Extract the verdict line (last non-empty line) from the narrative."""
    lines = [l.strip() for l in narrative.splitlines() if l.strip()]
    return lines[-1] if lines else ""

def _narrative_body(narrative: str) -> str:
    """Return all lines except the last (verdict) line."""
    lines = [l.strip() for l in narrative.splitlines() if l.strip()]
    return lines[:-1] if len(lines) > 1 else lines


def generate_report(
    shortlist: Shortlist,
    output_path: Path,
    assumptions: FinancialAssumptions | None = None,
) -> Path:
    """Render a self-contained HTML report and write it to output_path."""
    html = _render(shortlist, assumptions)
    output_path.write_text(html, encoding="utf-8")
    return output_path


# ── Formatters ─────────────────────────────────────────────────────────────────

def _fmt_currency(val: float | None, default: str = "—") -> str:
    return f"${val:,.0f}" if val is not None else default

def _fmt_pct(val: float | None) -> str:
    return f"{val:.2%}" if val is not None else "—"

def _fmt_cashflow(val: float | None) -> str:
    if val is None:
        return "—"
    sign = "+" if val >= 0 else ""
    return f"{sign}${val:,.0f}/mo"

def _render_assessed(deal) -> str:
    if not deal.tax_assessed_value:
        return ""
    if deal.tax_assessed_land and deal.tax_assessed_improvement:
        pct_land = deal.tax_assessed_land / deal.tax_assessed_value * 100
        pct_impr = deal.tax_assessed_improvement / deal.tax_assessed_value * 100
        breakdown = (
            f'<div class="assessed-breakdown">'
            f'<span title="Land value">🏞 {_fmt_currency(deal.tax_assessed_land)} ({pct_land:.0f}%)</span>'
            f'<span title="Building value">🏠 {_fmt_currency(deal.tax_assessed_improvement)} ({pct_impr:.0f}%)</span>'
            f'</div>'
        )
    else:
        breakdown = ""
    return (
        f'<div class="metric metric--assessed">'
        f'<div class="metric-label">Tax Assessed (Total)</div>'
        f'<div class="metric-value">{_fmt_currency(deal.tax_assessed_value)}</div>'
        f'{breakdown}'
        f'</div>'
    )


def _cashflow_class(val: float | None) -> str:
    if val is None:
        return ""
    return "positive" if val >= 0 else "negative"


# ── Photo helpers ───────────────────────────────────────────────────────────────

def _photo_div(deal: DealNarrative) -> str:
    """Return an img tag for the primary listing photo, or a fallback placeholder."""
    target = deal.listing_url or f"https://www.redfin.com/search#location={quote(deal.address)}"

    if deal.photo_url:
        return f"""<a href="{_esc(target)}" target="_blank" rel="noopener">
  <img src="{_esc(deal.photo_url)}" alt="{_esc(deal.address)}" class="listing-photo" loading="lazy" />
</a>"""

    # Fallback: address text on a neutral background
    query = quote(deal.address)
    return f"""<a href="https://www.google.com/maps/search/?api=1&query={query}"
   target="_blank" rel="noopener" class="photo-placeholder">
  <span>📍 {_esc(deal.address.split(',')[0])}</span>
</a>"""


# ── Card renderer ──────────────────────────────────────────────────────────────

def _render_zoning_potential(deal) -> str:
    zp = deal.zoning_potential
    if not zp:
        return ""

    score = zp.development_score or 0
    score_colors = {1: "#94a3b8", 2: "#64748b", 3: "#f59e0b", 4: "#3b82f6", 5: "#16a34a"}
    color = score_colors.get(score, "#94a3b8")
    stars = "★" * score + "☆" * (5 - score)

    badges = []
    if zp.dadu_eligible:
        badges.append('<span class="zp-badge zp-badge--green">✓ DADU eligible</span>')
    if zp.adu_eligible and not zp.dadu_eligible:
        badges.append('<span class="zp-badge zp-badge--blue">✓ ADU eligible</span>')
    if zp.subdivision_eligible:
        badges.append('<span class="zp-badge zp-badge--purple">✓ Subdivision possible</span>')
    if zp.hb1110_duplex:
        badges.append('<span class="zp-badge zp-badge--gray">WA HB 1110 duplex</span>')
    if zp.zoned_units and zp.zoned_units > 1:
        badges.append(f'<span class="zp-badge zp-badge--blue">Zoned for {zp.zoned_units} units</span>')

    opps_html = ""
    if zp.opportunities:
        # Skip first bullet if it duplicates the summary text
        opps = [o for o in zp.opportunities if not (zp.summary and o.strip() == zp.summary.strip())]
        if opps:
            items = "".join(f"<li>{o}</li>" for o in opps)
            opps_html = f'<ul class="zp-opps">{items}</ul>'

    summary_html = f'<p class="zp-summary">{zp.summary}</p>' if zp.summary else ""

    return f"""<div class="zoning-potential">
  <div class="zp-header">
    <span class="zp-title">Development Potential</span>
    <span class="zp-score" style="color:{color}" title="{score}/5">{stars}</span>
  </div>
  <div class="zp-badges">{"".join(badges)}</div>
  {summary_html}
  {opps_html}
</div>"""


def _render_schools(deal: DealNarrative) -> str:
    schools = deal.nearby_schools
    if not schools:
        return ""

    rows = []
    for s in schools[:5]:  # cap at 5 to keep card height reasonable
        dist = f"{s.distance_miles:.1f} mi" if s.distance_miles is not None else ""
        score_html = ""
        if s.proficiency_score is not None:
            pct = s.proficiency_score
            color = "#16a34a" if pct >= 70 else "#f59e0b" if pct >= 50 else "#dc2626"
            score_html = f' <span style="color:{color};font-weight:700">{pct:.0f}%</span>'
        rows.append(
            f'<li><span class="school-level">{_esc(s.level[:2].upper())}</span>'
            f' {_esc(s.name)}{score_html}'
            f'{f" · {dist}" if dist else ""}</li>'
        )

    return f"""<div class="schools">
  <div class="schools-header">Nearby Schools</div>
  <ul class="schools-list">{"".join(rows)}</ul>
</div>"""


def _render_appreciation(deal: DealNarrative) -> str:
    ap = deal.appreciation
    if not ap or not ap.signals:
        return ""

    score = ap.appreciation_score or 1
    score_colors = {1: "#94a3b8", 2: "#64748b", 3: "#f59e0b", 4: "#3b82f6", 5: "#16a34a"}
    color = score_colors.get(score, "#94a3b8")
    stars = "★" * score + "☆" * (5 - score)

    items = "".join(f"<li>{s}</li>" for s in ap.signals)
    return f"""<div class="appreciation">
  <div class="ap-header">
    <span class="ap-title">Appreciation Signals</span>
    <span class="ap-score" style="color:{color}" title="{score}/5">{stars}</span>
  </div>
  <ul class="ap-signals">{items}</ul>
</div>"""


_TEMPERATURE_COLOR = {
    "Seller's market": "#dc2626",
    "Balanced market": "#ca8a04",
    "Buyer's market": "#16a34a",
}


def _market_tile(label: str, value: str, hint: str = "") -> str:
    title = f' title="{hint}"' if hint else ""
    return (
        f'<div class="mkt-tile"{title}>'
        f'<div class="mkt-tile-label">{label}</div>'
        f'<div class="mkt-tile-value">{value}</div>'
        f"</div>"
    )


def _render_market_snapshot(deal: DealNarrative) -> str:
    """
    Area market context for the city this deal sits in.

    Renders nothing when no local slice covers the city — the strip is additive
    context, never a hard dependency (run `scout.py market-refresh` to populate).

    Percentages are suppressed below market_trends.MIN_SALES_FOR_RATES, because
    "100% sold above list" computed from a single sale is worse than showing
    nothing. Price drops are always shown next to sale-to-list: both price
    metrics use the *final* list price, so a home that cut its price and sold at
    the reduced ask still scores ~1.0, and the drop rate is the counterweight.
    """
    # Prefer what the pipeline attached (the same data the ranker reasoned over,
    # so strip and narrative can't disagree). Fall back to a local lookup for
    # shortlists saved before market context was carried through the pipeline.
    snap = deal.market_context or market_trends.snapshot_for_address(deal.address)
    if snap is None:
        return ""

    tiles: list[str] = []

    temperature = snap.market_temperature
    if temperature and snap.months_of_supply is not None:
        color = _TEMPERATURE_COLOR.get(temperature, "#64748b")
        tiles.append(
            f'<div class="mkt-tile" title="Months of supply: under 3 favours sellers, over 6 favours buyers.">'
            f'<div class="mkt-tile-label">Market</div>'
            f'<div class="mkt-tile-value" style="color:{color}">{temperature}'
            f'<span class="mkt-sub"> · {snap.months_of_supply:.1f} mo supply</span></div>'
            f"</div>"
        )

    if snap.has_enough_sales:
        if snap.sale_to_list is not None:
            pct = (snap.sale_to_list - 1) * 100
            direction = "over" if pct >= 0 else "under"
            tiles.append(_market_tile(
                "Sale-to-list",
                f"{snap.sale_to_list:.3f}<span class=\"mkt-sub\"> · {abs(pct):.1f}% {direction} ask</span>",
                "Average ratio of final sale price to final list price, for homes that "
                "closed this period. Uses the final list price, so price cuts are not "
                "reflected here — see price drops.",
            ))
        if snap.pct_above_list is not None:
            tiles.append(_market_tile(
                "Sold above ask",
                f"{snap.pct_above_list:.0%}",
                "Share of homes that closed above their most recent list price.",
            ))
        if snap.pct_price_drops is not None:
            tiles.append(_market_tile(
                "Price drops",
                f"{snap.pct_price_drops:.0%}",
                "Share of active listings that cut their price — the counterweight to "
                "sale-to-list, which is measured against the reduced price.",
            ))

    if snap.median_dom is not None:
        tiles.append(_market_tile(
            "Median days to contract",
            f"{snap.median_dom:.0f}",
            "Median days from listing to going under contract, for homes that went "
            "pending this period. Closing typically adds another 30-45 days.",
        ))

    if not tiles:
        return ""

    # Deal-vs-market: the part a generic market dashboard cannot show.
    # Gated on has_enough_sales like the other rates — a median $/sqft drawn from
    # one sale makes "100% above the city median" out of a single data point.
    comparison = ""
    if snap.has_enough_sales and deal.price and deal.sqft and snap.median_ppsf:
        deal_ppsf = deal.price / deal.sqft
        delta = (deal_ppsf / snap.median_ppsf - 1) * 100
        city = escape(snap.city)
        if abs(delta) < 1:
            phrase = f"in line with the {city} median"
            color = "#475569"
        else:
            word = "above" if delta > 0 else "below"
            color = "#b45309" if delta > 0 else "#15803d"
            phrase = f"{abs(delta):.0f}% {word} the {city} median"
        comparison = (
            f'<div class="mkt-compare">This home is <strong>${deal_ppsf:,.0f}/sqft</strong> — '
            f'<span style="color:{color}">{phrase}</span> of ${snap.median_ppsf:,.0f}/sqft.</div>'
        )

    sample_note = ""
    if not snap.has_enough_sales:
        sold = snap.homes_sold if snap.homes_sold is not None else 0
        sample_note = (
            f'<div class="mkt-note">Only {sold} '
            f"{'home' if sold == 1 else 'homes'} sold here this period — too few for "
            f"reliable sale-price percentages, so they are hidden.</div>"
        )

    return f"""<div class="market-snapshot">
  <div class="mkt-header">
    <span class="mkt-title">Market Context · {escape(snap.city)}, {escape(snap.state)}</span>
    <span class="mkt-asof">data as of {escape(snap.period_end)}</span>
  </div>
  <div class="mkt-tiles">{"".join(tiles)}</div>
  {comparison}
  {sample_note}
  <div class="mkt-source">Area data provided by Redfin. Monthly; typically 1–2 months behind.</div>
</div>"""


def _walk_score_meta(score: int) -> tuple[str, str]:
    """Return (label, hex_color) for a Walk Score value."""
    if score >= 90:
        return "Walker's Paradise", "#16a34a"
    if score >= 70:
        return "Very Walkable", "#65a30d"
    if score >= 50:
        return "Somewhat Walkable", "#ca8a04"
    if score >= 25:
        return "Car-Dependent", "#ea580c"
    return "Almost No Errands", "#dc2626"


def _render_walk_score_metric(deal: DealNarrative) -> str:
    if deal.walk_score is None:
        addr_enc = quote(deal.address, safe="")
        return (
            f'<div class="metric">'
            f'<div class="metric-label">Walk Score</div>'
            f'<div class="metric-value metric-value--link">'
            f'<a href="https://www.walkscore.com/score/{addr_enc}" target="_blank" rel="noopener">Look up →</a>'
            f'</div></div>'
        )
    label, color = _walk_score_meta(deal.walk_score)
    bar_pct = deal.walk_score
    return (
        f'<div class="metric metric--walkscore">'
        f'<div class="metric-label">Walk Score</div>'
        f'<div class="metric-value" style="color:{color}">{deal.walk_score}'
        f'<span class="walkscore-label"> {label}</span></div>'
        f'<div class="walkscore-bar">'
        f'<div class="walkscore-bar__fill" style="width:{bar_pct}%;background:{color}"></div>'
        f'</div>'
        f'</div>'
    )


def _render_transit_scores(deal: DealNarrative) -> str:
    """Render Bike Score and Transit Score as a compact pair (only if at least one is present)."""
    if deal.bike_score is None and deal.transit_score is None:
        return ""

    def _score_block(label: str, score: int | None, icon: str) -> str:
        if score is None:
            return f'<div class="score-pill score-pill--na">{icon} {label} —</div>'
        if score >= 70:
            color = "#16a34a"
        elif score >= 50:
            color = "#ca8a04"
        else:
            color = "#dc2626"
        return (
            f'<div class="score-pill" style="color:{color}">'
            f'{icon} {label} <strong>{score}</strong>'
            f'<div class="score-bar"><div class="score-bar__fill" style="width:{score}%;background:{color}"></div></div>'
            f'</div>'
        )

    bike = _score_block("Bike", deal.bike_score, "🚲")
    transit = _score_block("Transit", deal.transit_score, "🚌")
    return (
        f'<div class="metric metric--transit-scores">'
        f'<div class="metric-label">Bike &amp; Transit</div>'
        f'<div class="transit-score-row">{bike}{transit}</div>'
        f'</div>'
    )


_SOLAR_SCORE_COLORS = {1: "#94a3b8", 2: "#64748b", 3: "#f59e0b", 4: "#3b82f6", 5: "#16a34a"}
_SOLAR_SCORE_LABELS = {1: "Low", 2: "Below avg", 3: "Average", 4: "Good", 5: "Excellent"}


def _render_solar_metric(deal: DealNarrative) -> str:
    if deal.solar_ghi_annual is None:
        return ""
    score = solar_score(deal.solar_ghi_annual)
    color = _SOLAR_SCORE_COLORS.get(score or 1, "#94a3b8")
    label = _SOLAR_SCORE_LABELS.get(score or 1, "")
    dot = f'<span style="color:{color};font-size:0.7rem;margin-right:0.2rem">●</span>'
    return (
        f'<div class="metric">'
        f'<div class="metric-label">Sun Exposure</div>'
        f'<div class="metric-value">{dot}{deal.solar_ghi_annual:.1f} hrs/day'
        f'<span class="solar-label"> {label}</span></div>'
        f'</div>'
    )


def _render_features(deal: DealNarrative) -> str:
    """Render scraped Redfin property features as compact chips."""
    chips = []
    if deal.has_primary_suite is True:
        chips.append("🛏 Primary suite")
    if deal.has_garage is True:
        label = f"🚗 {deal.garage_spaces}-car garage" if deal.garage_spaces else "🚗 Garage"
        chips.append(label)
    if deal.has_basement is True:
        chips.append("⬇ Finished basement" if deal.basement_finished else "⬇ Basement")
    if deal.has_fireplace is True:
        chips.append("🔥 Fireplace")
    if not chips:
        return ""
    items = "".join(f'<span class="feature-chip">{c}</span>' for c in chips)
    return f'<div class="features">{items}</div>'


def _verdict_reasons(deal: DealNarrative, purpose: str = "rental") -> list[str]:
    """Generate 2–4 specific data-driven reasons for the verdict."""
    reasons = []
    verdict = _verdict_label(deal.narrative).lower()

    risk = deal.risk_level
    hoa = deal.hoa_fee
    dom = deal.days_on_market
    flood = deal.flood_zone

    if purpose == "primary":
        # Primary residence: focus on affordability and livability
        piti = deal.monthly_piti
        ws = deal.walk_score

        if piti is not None:
            if piti > 8000:
                reasons.append(f"High monthly PITI of ${piti:,.0f} — verify this fits your budget")
            elif piti > 5000:
                reasons.append(f"Monthly PITI of ${piti:,.0f} — substantial housing cost")
            else:
                reasons.append(f"Monthly PITI of ${piti:,.0f}")

        if ws is not None:
            if ws >= 90:
                reasons.append(f"Walk Score {ws} — Walker's Paradise, minimal car dependence")
            elif ws >= 70:
                reasons.append(f"Walk Score {ws} — Very Walkable for daily errands")
            elif ws >= 50:
                reasons.append(f"Walk Score {ws} — Some walkable amenities nearby")
            else:
                reasons.append(f"Walk Score {ws} — Car-dependent area; verify commute")

        if hoa and hoa > 0:
            reasons.append(f"HOA fee ${hoa:,.0f}/mo adds to monthly housing cost")

        if risk == "HIGH":
            reasons.append("High risk flag — check flood zone, zoning, or structural concerns")

        if flood and flood not in ("X", "X500", ""):
            reasons.append(f"Flood zone {flood} — flood insurance likely required")

        if dom is not None and dom > 90:
            reasons.append(f"{dom} days on market — sellers may be motivated to negotiate on price")
    else:
        cap = deal.cap_rate
        cf = deal.monthly_cashflow

        # Cap rate assessment
        if cap is not None:
            cap_pct = f"{cap:.1%}"
            if cap < 0.02:
                reasons.append(f"Cap rate of {cap_pct} is well below a typical 5% investment threshold")
            elif cap < 0.04:
                reasons.append(f"Cap rate of {cap_pct} falls short of the 5% target — limited income relative to price")
            elif cap < 0.05:
                reasons.append(f"Cap rate of {cap_pct} is close to but slightly under the 5% target")
            elif cap >= 0.06:
                reasons.append(f"Strong cap rate of {cap_pct} — well above the 5% benchmark")
            else:
                reasons.append(f"Cap rate of {cap_pct} meets the investment threshold")

        # Cash flow assessment
        if cf is not None:
            cf_str = f"${cf:+,.0f}/mo"
            if cf < -500:
                reasons.append(f"Deeply negative cash flow ({cf_str}) means out-of-pocket losses every month")
            elif cf < -200:
                reasons.append(f"Negative cash flow ({cf_str}) requires monthly top-up from personal funds")
            elif cf < 0:
                reasons.append(f"Slightly negative cash flow ({cf_str}) — manageable but needs monitoring")
            elif cf >= 300:
                reasons.append(f"Healthy positive cash flow ({cf_str}) provides a solid income buffer")
            else:
                reasons.append(f"Near break-even cash flow ({cf_str})")

        if risk == "HIGH":
            reasons.append("High risk flag — check flood zone, zoning restrictions, or other structural concerns")
        elif risk == "MEDIUM" and "pass" in verdict:
            reasons.append("Medium risk adds uncertainty on top of weak financials")

        if hoa and hoa > 400:
            reasons.append(f"High HOA fee (${hoa:,.0f}/mo) significantly reduces net income")
        elif hoa and hoa > 200 and deal.cap_rate is not None and deal.cap_rate < 0.04:
            reasons.append(f"HOA fee (${hoa:,.0f}/mo) compounds the weak cap rate")

        if flood and flood not in ("X", "X500", ""):
            reasons.append(f"Flood zone {flood} may require expensive flood insurance")

        if dom is not None and dom > 60 and "pass" not in verdict:
            reasons.append(f"{dom} days on market suggests limited buyer interest — may signal a pricing issue")
        elif dom is not None and dom > 90:
            reasons.append(f"Over {dom} days on market — sellers may be motivated to negotiate")

    # Add actual risk flags from pipeline
    for flag_desc in (deal.risk_flags or []):
        if flag_desc not in reasons:
            reasons.append(flag_desc)

    return reasons[:6]  # cap at 6 bullets


def _render_verdict_reasons(deal: DealNarrative, purpose: str = "rental") -> str:
    reasons = _verdict_reasons(deal, purpose)
    if not reasons:
        return ""
    # Reasons interpolate feed values (flood zone) and pipeline risk flags, so
    # escape here rather than at each of the ~10 places that build one.
    items = "".join(f"<li>{_esc(r)}</li>" for r in reasons)
    return f'<ul class="verdict-reasons">{items}</ul>'


_TARGET_CAP_RATE = 0.05  # 5% — standard threshold used across scoring
# Portion of NOI expenses that scale with purchase price (maintenance 1% + tax 1.2%).
_PRICE_DEPENDENT_EXPENSE_RATE = 0.01 + 0.012
# Portion of rent that flows to NOI after vacancy (8%) and management (10%).
_RENT_TO_NOI_FACTOR = (1 - 0.08) * (1 - 0.10)  # ≈ 0.828


_DEFAULT_DOWN_PCT = 0.25
_DEFAULT_LOAN_YEARS = 30


def _noi_at_price(noi0: float, p0: float, p_new: float) -> float:
    """Recalculate NOI at a different price, accounting for price-dependent expenses."""
    k = _PRICE_DEPENDENT_EXPENSE_RATE
    A = noi0 + k * p0
    return A - k * p_new


def _monthly_payment(loan: float, annual_rate: float, years: int = _DEFAULT_LOAN_YEARS) -> float:
    """Standard fixed-rate mortgage payment formula."""
    r = annual_rate / 12
    n = years * 12
    if r == 0:
        return loan / n
    return loan * r * (1 + r) ** n / ((1 + r) ** n - 1)


def _derive_rate(monthly_payment: float, price: float,
                 down_pct: float = _DEFAULT_DOWN_PCT,
                 years: int = _DEFAULT_LOAN_YEARS) -> float:
    """Back-calculate the annual interest rate from an existing mortgage payment via bisection."""
    loan = price * (1 - down_pct)
    lo, hi = 0.001, 0.20
    for _ in range(60):
        mid = (lo + hi) / 2
        if _monthly_payment(loan, mid, years) > monthly_payment:
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2


def _render_price_targets(deal: DealNarrative) -> str:
    """
    For Proceed with Caution / Pass verdicts, show a practical investment analysis:
    - What the metrics look like at realistic negotiated prices (-10%, -15%, -20%)
    - What monthly rent would be needed (at current price) to reach 5% cap rate
    - A plain-language bottom-line assessment
    """
    verdict = _verdict_label(deal.narrative).lower()
    if not any(v in verdict for v in ("proceed with caution", "pass")):
        return ""
    if not deal.noi_annual or not deal.price or deal.price <= 0:
        return ""

    p0 = deal.price
    noi0 = deal.noi_annual
    rent0 = deal.estimated_monthly_rent or 0.0
    mortgage0 = deal.monthly_mortgage or 0.0

    # ── Derive current interest rate from existing mortgage payment ────────────
    current_rate = None
    if mortgage0 > 0 and p0 > 0:
        try:
            current_rate = _derive_rate(mortgage0, p0)
        except Exception:
            pass
    current_rate = current_rate or 0.07  # fall back to 7% if derivation fails
    loan0 = p0 * (1 - _DEFAULT_DOWN_PCT)

    # ── Scenario table: price cuts at current rate ─────────────────────────────
    scenarios = [("−10%", 0.90), ("−15%", 0.85), ("−20%", 0.80)]
    scenario_rows = []
    best_cap_in_scenarios = 0.0
    for label, factor in scenarios:
        p_new = p0 * factor
        noi_new = _noi_at_price(noi0, p0, p_new)
        cap_new = noi_new / p_new if p_new > 0 else 0
        best_cap_in_scenarios = max(best_cap_in_scenarios, cap_new)
        mortgage_new = _monthly_payment(p_new * (1 - _DEFAULT_DOWN_PCT), current_rate)
        cf_new = noi_new / 12 - mortgage_new
        cap_cls = "pt-ok" if cap_new >= _TARGET_CAP_RATE * 0.8 else "pt-hard"
        cf_cls = "pt-ok" if cf_new >= 0 else "pt-hard"
        scenario_rows.append(
            f"<tr>"
            f"<td>{label} ({_fmt_currency(p_new)})</td>"
            f"<td class='{cap_cls}'>{cap_new:.2%}</td>"
            f"<td class='{cf_cls}'>{_fmt_cashflow(cf_new)}</td>"
            f"</tr>"
        )

    # ── Rent needed to hit 5% cap at current price ────────────────────────────
    target_noi = p0 * _TARGET_CAP_RATE
    # NOI gap that rent must close (price-dependent expenses stay the same)
    noi_gap = target_noi - noi0
    rent_increase_needed = noi_gap / _RENT_TO_NOI_FACTOR / 12  # monthly
    rent_needed = rent0 + rent_increase_needed

    # ── Bottom-line assessment ─────────────────────────────────────────────────
    # Is there any realistic path to a workable investment?
    realistic_cap_close = best_cap_in_scenarios >= _TARGET_CAP_RATE * 0.7  # within 30% of target
    rent_increase_pct = (rent_needed / rent0 - 1) * 100 if rent0 > 0 else float("inf")
    rent_realistic = rent_increase_pct <= 20  # ≤20% rent increase is plausible

    if realistic_cap_close and rent_realistic:
        note = (
            f"A 15–20% price negotiation combined with modest rent growth could make this work. "
            f"Consider making an offer 15% below asking."
        )
        note_class = "pt-note pt-note--ok"
    elif realistic_cap_close:
        note = (
            f"A 15–20% price cut brings the cap rate closer to viable — "
            f"but rent would also need to reach {_fmt_currency(rent_needed)}/mo "
            f"(up {rent_increase_pct:.0f}% from current estimate) to fully close the gap. "
            f"Only makes sense if you expect strong rent appreciation."
        )
        note_class = "pt-note pt-note--ok"
    elif rent_realistic:
        note = (
            f"Even at a 20% price cut this property falls short of investment targets. "
            f"Rental income of {_fmt_currency(rent_needed)}/mo "
            f"(+{rent_increase_pct:.0f}% vs current estimate) would be needed at today's price — "
            f"verify local rents before dismissing entirely."
        )
        note_class = "pt-note pt-note--hard"
    else:
        note = (
            f"No realistic path at current market conditions: a 20% price cut still leaves the "
            f"cap rate far below target, and hitting 5% would require rent of "
            f"{_fmt_currency(rent_needed)}/mo (+{rent_increase_pct:.0f}%). "
            f"This is a poor investment property at this price — consider it appreciation-only."
        )
        note_class = "pt-note pt-note--hard"

    # ── Rate sensitivity: what if rates drop 0.5% or 1% ──────────────────────
    rate_rows = []
    rate_scenarios = []
    if current_rate > 0.065:
        rate_scenarios.append((f"{(current_rate - 0.005) * 100:.1f}%", current_rate - 0.005))
    if current_rate > 0.055:
        rate_scenarios.append((f"{(current_rate - 0.010) * 100:.1f}%", current_rate - 0.010))
    rate_scenarios.append((f"{(current_rate - 0.015) * 100:.1f}%", current_rate - 0.015))

    for rate_label, rate in rate_scenarios:
        if rate <= 0:
            continue
        # At current price, new rate
        cf_rate_only = noi0 / 12 - _monthly_payment(loan0, rate)
        # At -15% price, new rate (combined scenario)
        p15 = p0 * 0.85
        noi15 = _noi_at_price(noi0, p0, p15)
        cf_combined = noi15 / 12 - _monthly_payment(p15 * (1 - _DEFAULT_DOWN_PCT), rate)
        cf_cls = "pt-ok" if cf_rate_only >= 0 else ("pt-ok" if cf_rate_only > -500 else "pt-hard")
        cf_combined_cls = "pt-ok" if cf_combined >= 0 else "pt-hard"
        rate_rows.append(
            f"<tr>"
            f"<td>{rate_label} rate, current price</td>"
            f"<td>—</td>"
            f"<td class='{cf_cls}'>{_fmt_cashflow(cf_rate_only)}</td>"
            f"</tr>"
            f"<tr>"
            f"<td>{rate_label} rate + −15% price</td>"
            f"<td>—</td>"
            f"<td class='{cf_combined_cls}'>{_fmt_cashflow(cf_combined)}</td>"
            f"</tr>"
        )

    scenario_rows_html = "\n".join(scenario_rows)
    rate_rows_html = "\n".join(rate_rows)
    rate_section = (
        f'<div class="pt-rate-header">If interest rates drop (from current ~{current_rate*100:.1f}%)</div>'
        f'<table class="pt-table">'
        f'<thead><tr><th>Scenario</th><th>Cap rate</th><th>Monthly cash flow</th></tr></thead>'
        f'<tbody>{rate_rows_html}</tbody>'
        f'</table>'
    ) if rate_rows_html else ""

    rent_row = (
        f'<div class="pt-rent-note">'
        f'Rent needed at current price for 5% cap rate: '
        f'<strong>{_fmt_currency(rent_needed)}/mo</strong> '
        f'(current estimate: {_fmt_currency(rent0)}/mo, +{rent_increase_pct:.0f}%)'
        f'</div>'
        if rent0 > 0 else ""
    )

    return f"""
<div class="price-targets">
  <div class="pt-title">What would it take to make this deal work?</div>
  <div class="pt-rate-label">At current rate ~{current_rate*100:.1f}% — price negotiation scenarios</div>
  <table class="pt-table">
    <thead><tr><th>Negotiated price</th><th>Cap rate</th><th>Monthly cash flow</th></tr></thead>
    <tbody>{scenario_rows_html}</tbody>
  </table>
  {rate_section}
  {rent_row}
  <p class="{note_class}">{note}</p>
</div>"""


def _render_deal(deal: DealNarrative, idx: int, purpose: str = "rental") -> str:
    risk = _RISK_COLOR.get(deal.risk_level, _RISK_COLOR["LOW"])
    cf_class = _cashflow_class(deal.monthly_cashflow)

    beds = f"{deal.beds} bd" if deal.beds else "—"
    baths = f"{deal.baths} ba" if deal.baths else "—"
    _dom_n = deal.days_on_market
    dom = f"{_dom_n} {'day' if _dom_n == 1 else 'days'} on market" if _dom_n is not None else "—"
    sqft = f"{deal.sqft:,} sqft" if deal.sqft else "—"
    lot = f"{deal.lot_sqft:,} sqft lot" if deal.lot_sqft else ""
    year = f"Built {deal.year_built}" if deal.year_built else ""

    redfin_btn = ""
    if deal.listing_url:
        redfin_btn = f'<a href="{_esc(deal.listing_url)}" target="_blank" rel="noopener" class="redfin-btn">View on Redfin →</a>'

    photo_html = _photo_div(deal)
    narrative_html = "".join(f"<p>{_esc(line)}</p>" for line in _narrative_body(deal.narrative))
    _vl = _verdict_label(deal.narrative)
    if _vl:
        _vs = _verdict_style(deal.narrative)
        verdict_html = (
            f'<div class="verdict-banner" style="background:{_vs["bg"]};'
            f'color:{_vs["text"]};border-color:{_vs["border"]}">{_esc(_vl)}</div>'
        )
    else:
        verdict_html = ""

    # data attributes for JS recalculation (rental only)
    noi_attr = f' data-noi-annual="{deal.noi_annual}"' if deal.noi_annual is not None else ""
    price_attr = f' data-price="{deal.price}"' if deal.price else ""

    if purpose == "primary":
        piti_class = ""  # no positive/negative coloring for PITI
        financial_metrics = f"""
      <div class="metric">
        <div class="metric-label">Price</div>
        <div class="metric-value">{_fmt_currency(deal.price)}</div>
      </div>
      <div class="metric">
        <div class="metric-label">Monthly PITI</div>
        <div class="metric-value">{_fmt_currency(deal.monthly_piti)}/mo</div>
      </div>
      {f'<div class="metric"><div class="metric-label">HOA</div><div class="metric-value negative">{_fmt_currency(deal.hoa_fee)}/mo</div></div>' if deal.hoa_fee else ""}
      {f'<div class="metric"><div class="metric-label">Home Type</div><div class="metric-value">{_esc(deal.home_type)}</div></div>' if deal.home_type else ""}"""
    else:
        financial_metrics = f"""
      <div class="metric">
        <div class="metric-label">Price</div>
        <div class="metric-value">{_fmt_currency(deal.price)}</div>
      </div>
      <div class="metric">
        <div class="metric-label">Cap Rate (net)</div>
        <div class="metric-value">{_fmt_pct(deal.cap_rate)}</div>
      </div>
      <div class="metric">
        <div class="metric-label">Cash-on-Cash</div>
        <div class="metric-value js-coc {_cashflow_class(deal.coc_return)}">{_fmt_pct(deal.coc_return)}</div>
      </div>
      <div class="metric">
        <div class="metric-label">Monthly Cash Flow</div>
        <div class="metric-value js-cashflow {cf_class}">{_fmt_cashflow(deal.monthly_cashflow)}</div>
      </div>
      {f'<div class="metric"><div class="metric-label">HOA</div><div class="metric-value negative">{_fmt_currency(deal.hoa_fee)}/mo</div></div>' if deal.hoa_fee else ""}
      {f'<div class="metric"><div class="metric-label">Home Type</div><div class="metric-value">{_esc(deal.home_type)}</div></div>' if deal.home_type else ""}"""

    price_targets_html = "" if purpose == "primary" else _render_price_targets(deal)

    return f"""
<div class="card{'  card--first' if deal.rank == 1 else ''}" data-rank="{deal.rank}"{price_attr}{noi_attr}>
  <div class="card-photo">{photo_html}</div>
  <div class="card-body">

    <div class="card-header">
      <div class="rank-badge">#{deal.rank}</div>
      <div class="card-title-block">
        <div class="card-address">{_esc(deal.address)}</div>
        <div class="card-meta">{beds} &nbsp;/&nbsp; {baths} &nbsp;·&nbsp; {sqft}
          {f"&nbsp;·&nbsp; {lot}" if lot else ""}
          {f"&nbsp;·&nbsp; {year}" if year else ""}
          &nbsp;·&nbsp; {dom}
        </div>
      </div>
      <div class="risk-badge" style="background:{risk['bg']};color:{risk['text']};border-color:{risk['border']}">
        {_RISK_LABEL.get(deal.risk_level, deal.risk_level)}
      </div>
    </div>

    <div class="metrics-grid">
      {financial_metrics}
      {_render_assessed(deal)}
      {_render_walk_score_metric(deal)}
      {_render_transit_scores(deal)}
      {_render_solar_metric(deal)}

      {f'<div class="metric"><div class="metric-label">Flood Zone</div><div class="metric-value">{_esc(deal.flood_zone)}</div></div>' if deal.flood_zone else ""}
      {f'<div class="metric"><div class="metric-label">Zoning</div><div class="metric-value zoning">{_esc(deal.zoning)}</div></div>' if deal.zoning else ""}
    </div>

    {_render_features(deal)}

    <div class="narrative">{narrative_html}</div>
    {verdict_html}
    {_render_verdict_reasons(deal, purpose)}
    {price_targets_html}

    {_render_schools(deal)}
    {_render_zoning_potential(deal)}
    {_render_appreciation(deal)}
    {_render_market_snapshot(deal)}

    {redfin_btn}
  </div>
</div>"""


# ── Full page ──────────────────────────────────────────────────────────────────

def _render_map(shortlist: Shortlist) -> str:
    """Leaflet.js map with numbered, color-coded markers for all properties that have coordinates."""
    map_deals = [d for d in shortlist.deals if d.latitude is not None and d.longitude is not None]
    if not map_deals:
        return ""

    map_data = _json_for_script([
        {
            "rank": d.rank,
            "address": d.address,
            "price": d.price,
            "lat": d.latitude,
            "lng": d.longitude,
            "cap_rate": d.cap_rate,
            "cashflow": d.monthly_cashflow,
            "verdict": _verdict_label(d.narrative),
            "url": d.listing_url or "",
        }
        for d in map_deals
    ])

    # Use string concatenation so JS braces don't need escaping
    js = (
        "(function() {\n"
        "  var deals = " + map_data + ";\n"
        "  if (!deals.length || typeof L === 'undefined') return;\n"
        "  var map = L.map('property-map');\n"
        "  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {\n"
        "    attribution: '&copy; <a href=\"https://www.openstreetmap.org/copyright\">OpenStreetMap</a> contributors',\n"
        "    maxZoom: 19\n"
        "  }).addTo(map);\n"
        "  var VC = {'strong buy':'#16a34a','buy':'#22c55e','consider':'#3b82f6','proceed with caution':'#f59e0b','pass':'#ef4444'};\n"
        "  function vcolor(v) { var l=(v||'').toLowerCase(); for (var k in VC) { if (l.indexOf(k)!==-1) return VC[k]; } return '#6b7280'; }\n"
        "  function fp(p) { return '$'+Math.round(p).toLocaleString(); }\n"
        "  function fc(c) { return c!=null?(c*100).toFixed(2)+'%':'—'; }\n"
        "  function ff(c) { return c!=null?((c>=0?'+':'')+' $'+Math.round(c).toLocaleString()+'/mo'):'—'; }\n"
        "  var markers = [];\n"
        "  for (var i=0; i<deals.length; i++) {\n"
        "    var d=deals[i], color=vcolor(d.verdict);\n"
        "    var icon = L.divIcon({\n"
        "      className: '',\n"
        "      html: '<div style=\"background:'+color+';color:#fff;font-weight:700;font-size:11px;width:26px;height:26px;border-radius:50%;border:2.5px solid #fff;box-shadow:0 2px 6px rgba(0,0,0,.4);display:flex;align-items:center;justify-content:center\">' + d.rank + '</div>',\n"
        "      iconSize: [26,26], iconAnchor: [13,13], popupAnchor: [0,-16]\n"
        "    });\n"
        "    var street=d.address.split(',')[0];\n"
        "    var city=d.address.split(',').slice(1).join(',').trim();\n"
        "    var redfin=d.url?'<br><a href=\"'+d.url+'\" target=\"_blank\" style=\"color:#2563eb;font-size:12px\">View on Redfin →</a>':'';\n"
        "    var popup='<div style=\"min-width:210px;font-family:-apple-system,sans-serif\">'\n"
        "      +'<div style=\"font-weight:700;font-size:14px\">#'+d.rank+' '+street+'</div>'\n"
        "      +'<div style=\"color:#64748b;font-size:12px;margin-bottom:6px\">'+city+'</div>'\n"
        "      +'<div style=\"font-size:13px;line-height:1.9\">'\n"
        "      +'💰 '+fp(d.price)+'<br>📊 Cap rate (net): '+fc(d.cap_rate)+'<br>💵 Cash flow: '+ff(d.cashflow)\n"
        "      +'</div>'\n"
        "      +'<div style=\"margin-top:5px;font-weight:700;color:'+color+'\">'+d.verdict+'</div>'\n"
        "      +redfin+'</div>';\n"
        "    var m=L.marker([d.lat,d.lng],{icon:icon}).addTo(map);\n"
        "    m.bindPopup(popup,{maxWidth:270});\n"
        "    markers.push(m);\n"
        "  }\n"
        "  if (markers.length===1) { map.setView([deals[0].lat,deals[0].lng],14); }\n"
        "  else { var g=L.featureGroup(markers); map.fitBounds(g.getBounds().pad(0.2)); }\n"
        "})();\n"
    )

    return (
        '<div class="map-section"><div id="property-map"></div></div>\n'
        "<script>\n" + js + "</script>\n"
    )


def _render_comparison_table(shortlist: Shortlist) -> str:
    """Side-by-side comparison table shown above cards when there are 2+ deals."""
    if len(shortlist.deals) < 2:
        return ""

    purpose = shortlist.purpose
    rows = []
    for d in shortlist.deals:
        verdict = _verdict_label(d.narrative)
        vstyle = _verdict_style(d.narrative)
        risk = _RISK_COLOR.get(d.risk_level, _RISK_COLOR["LOW"])

        price_str = f"${d.price:,.0f}" if d.price else "—"
        if d.beds and d.baths:
            bed_bath = f"{d.beds}bd / {d.baths:g}ba"
        elif d.beds:
            bed_bath = f"{d.beds}bd"
        else:
            bed_bath = "—"
        ws_str = str(d.walk_score) if d.walk_score is not None else "—"
        street = d.address.split(",")[0]
        risk_label = _RISK_LABEL.get(d.risk_level, d.risk_level)
        risk_style = (
            f"background:{risk['bg']};color:{risk['text']};"
            f"border-color:{risk['border']}"
        )
        verdict_style = (
            f"background:{vstyle['bg']};color:{vstyle['text']};"
            f"border:1px solid {vstyle['border']}"
        )

        if purpose == "primary":
            piti_str = f"${d.monthly_piti:,.0f}/mo" if d.monthly_piti is not None else "—"
            fin_cells = f'      <td class="ct-piti">{piti_str}</td>\n'
        else:
            cap_str = f"{d.cap_rate * 100:.2f}%" if d.cap_rate is not None else "—"
            if d.monthly_cashflow is not None:
                sign = "+" if d.monthly_cashflow >= 0 else ""
                cf_cls = "positive" if d.monthly_cashflow >= 0 else "negative"
                cf_str = f"{sign}${abs(round(d.monthly_cashflow)):,}/mo"
            else:
                cf_cls = ""
                cf_str = "—"
            fin_cells = (
                f'      <td class="ct-cap">{cap_str}</td>\n'
                f'      <td class="ct-cf {cf_cls}">{cf_str}</td>\n'
            )

        rows.append(
            f'    <tr data-rank="{d.rank}">\n'
            f'      <td class="ct-rank">{d.rank}</td>\n'
            f'      <td class="ct-address">{_esc(street)}</td>\n'
            f'      <td class="ct-price">{price_str}</td>\n'
            f'      <td class="ct-bedbath">{bed_bath}</td>\n'
            + fin_cells
            + f'      <td class="ct-ws">{ws_str}</td>\n'
            f'      <td class="ct-risk"><span class="risk-badge" style="{risk_style}">'
            f'{_esc(risk_label)}</span></td>\n'
            f'      <td class="ct-verdict"><span class="verdict-chip" style="{verdict_style}">'
            f'{_esc(verdict)}</span></td>\n'
            f'    </tr>'
        )

    if purpose == "primary":
        fin_headers = "        <th>Monthly PITI</th>\n"
        ct_note = "&nbsp;· PITI = principal, interest, taxes, insurance"
    else:
        fin_headers = "        <th>Cap Rate</th>\n        <th>Cash Flow</th>\n"
        ct_note = "&nbsp;· cap rate is net (excludes financing costs)"

    rows_html = "\n".join(rows)
    return (
        '<div class="comparison-table-wrap">\n'
        '  <div class="ct-header">'
        '<span class="ct-title">Side-by-Side Comparison</span>'
        f'<span class="ct-note">{ct_note}</span>'
        "</div>\n"
        '  <div class="ct-scroll">\n'
        '    <table class="comparison-table">\n'
        "      <thead><tr>\n"
        "        <th>#</th>\n"
        "        <th>Address</th>\n"
        "        <th>Price</th>\n"
        "        <th>Bed / Bath</th>\n"
        + fin_headers
        + "        <th>Walk</th>\n"
        "        <th>Risk</th>\n"
        "        <th>Verdict</th>\n"
        "      </tr></thead>\n"
        "      <tbody>\n"
        + rows_html
        + "\n      </tbody>\n"
        "    </table>\n"
        "  </div>\n"
        "</div>\n"
    )


def _render(shortlist: Shortlist, assumptions: FinancialAssumptions | None = None) -> str:
    purpose = shortlist.purpose
    cards_html = "\n".join(_render_deal(d, i, purpose) for i, d in enumerate(shortlist.deals))
    map_section = _render_map(shortlist)
    comparison_table = _render_comparison_table(shortlist)
    has_map = bool(map_section)
    count = len(shortlist.deals)

    # Slider defaults from assumptions (or fallbacks)
    default_down = int((assumptions.down_payment_pct if assumptions else 0.25) * 100)
    default_rate = (assumptions.loan_rate_annual if assumptions else 0.07) * 100
    default_term = assumptions.loan_term_years if assumptions else 30

    page_title = "Home Scout" if purpose == "primary" else "Deal Scout"
    subtitle = "Primary residence search" if purpose == "primary" else "Click any photo to open on Redfin"

    if purpose == "primary":
        sliders_html = ""
    else:
        show_filter = ""
        if count > 5:
            sel5 = "selected" if count <= 10 else ""
            sel10 = "selected" if count > 10 else ""
            show_filter = (
                f'  <label>\n    Show top:\n'
                f'    <select id="selectShow" onchange="applyShowFilter()">\n'
                f'      <option value="5" {sel5}>5</option>\n'
                f'      <option value="10" {sel10}>10</option>\n'
                f'      <option value="0">All</option>\n'
                f'    </select>\n  </label>\n'
            )
        sel15 = "selected" if default_term == 15 else ""
        sel20 = "selected" if default_term == 20 else ""
        sel30 = "selected" if default_term == 30 else ""
        sliders_html = (
            '<div class="sliders-bar">\n'
            '  <label>\n    Down payment:\n'
            f'    <input type="range" id="sliderDown" min="5" max="50" step="1" value="{default_down}"\n'
            f'           oninput="document.getElementById(\'valDown\').textContent=this.value+\'%\';recalcAll()">\n'
            f'    <span class="slider-val" id="valDown">{default_down}%</span>\n'
            '  </label>\n'
            '  <label>\n    Loan rate:\n'
            f'    <input type="range" id="sliderRate" min="3" max="12" step="0.25" value="{default_rate:.2f}"\n'
            f'           oninput="document.getElementById(\'valRate\').textContent=parseFloat(this.value).toFixed(2)+\'%\';recalcAll()">\n'
            f'    <span class="slider-val" id="valRate">{default_rate:.2f}%</span>\n'
            '  </label>\n'
            '  <label>\n    Term:\n'
            '    <select id="selectTerm" onchange="recalcAll()">\n'
            f'      <option value="15" {sel15}>15 yr</option>\n'
            f'      <option value="20" {sel20}>20 yr</option>\n'
            f'      <option value="30" {sel30}>30 yr</option>\n'
            '    </select>\n  </label>\n'
            + show_filter
            + '  <span class="recalc-note">Cash flow &amp; CoC update live · net cap rate excludes financing costs</span>\n'
            '</div>\n'
        )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{page_title} — {shortlist.market}</title>

<style>
/* ── Reset & base ── */
*, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
  background: #f8fafc;
  color: #1e293b;
  line-height: 1.5;
}}

/* ── Header ── */
.page-header {{
  background: linear-gradient(135deg, #1e3a5f 0%, #2563eb 100%);
  color: #fff;
  padding: 2.5rem 2rem 2rem;
}}
.page-header h1 {{
  font-size: 1.75rem;
  font-weight: 800;
  letter-spacing: -0.02em;
  margin-bottom: 0.25rem;
}}
.page-header .subtitle {{
  font-size: 0.95rem;
  opacity: 0.75;
  margin-bottom: 1rem;
}}
.summary-bar {{
  background: rgba(255,255,255,0.12);
  border: 1px solid rgba(255,255,255,0.2);
  border-radius: 8px;
  padding: 0.75rem 1rem;
  font-size: 0.875rem;
  max-width: 720px;
}}

/* ── Main layout ── */
/* Full-width: fills the viewport and reflows on resize. Side padding matches the
   header (2rem) so content left-aligns; the 700px media query tightens it on phones. */
.main {{ max-width: none; margin: 0; padding: 2rem 2rem; }}

/* ── Cards ── */
.card {{
  background: #fff;
  border: 1px solid #e2e8f0;
  border-radius: 12px;
  overflow: hidden;
  display: grid;
  grid-template-columns: 200px 1fr;
  margin-bottom: 1.5rem;
  box-shadow: 0 1px 3px rgba(0,0,0,0.06), 0 1px 2px rgba(0,0,0,0.04);
  transition: box-shadow 0.2s;
}}
.card:hover {{
  box-shadow: 0 4px 12px rgba(0,0,0,0.10), 0 2px 4px rgba(0,0,0,0.06);
}}
.card--first {{
  border-color: #2563eb;
  box-shadow: 0 0 0 2px #2563eb22, 0 4px 12px rgba(37,99,235,0.12);
}}

/* ── Photo pane ── */
.card-photo {{
  position: relative;
  height: 220px;
  background: #e2e8f0;
  overflow: hidden;
  flex-shrink: 0;
}}
.card-photo a {{ display: block; width: 100%; height: 100%; }}
.listing-photo {{
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
  transition: transform 0.3s ease;
}}
.card-photo:hover .listing-photo {{ transform: scale(1.03); }}
.photo-placeholder {{
  display: flex;
  align-items: center;
  justify-content: center;
  width: 100%;
  height: 100%;
  text-decoration: none;
  color: #475569;
  font-size: 0.875rem;
  font-weight: 500;
  background: #f1f5f9;
}}
.photo-placeholder:hover {{ background: #e2e8f0; }}

/* ── Card body ── */
.card-body {{ padding: 1.25rem 1.5rem; display: flex; flex-direction: column; gap: 1rem; }}

.card-header {{ display: flex; align-items: flex-start; gap: 0.75rem; }}
.rank-badge {{
  font-size: 1.5rem;
  font-weight: 900;
  color: #2563eb;
  min-width: 2rem;
  line-height: 1;
  padding-top: 0.1rem;
}}
.card--first .rank-badge {{ color: #1d4ed8; }}
.card-title-block {{ flex: 1; }}
.card-address {{
  font-size: 1rem;
  font-weight: 700;
  color: #0f172a;
  line-height: 1.3;
  margin-bottom: 0.2rem;
}}
.card-meta {{ font-size: 0.78rem; color: #64748b; }}
.risk-badge {{
  font-size: 0.7rem;
  font-weight: 700;
  padding: 0.2rem 0.65rem;
  border-radius: 99px;
  border: 1px solid;
  white-space: nowrap;
  margin-top: 0.1rem;
}}

/* ── Metrics grid ── */
.metrics-grid {{
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(130px, 1fr));
  gap: 0.5rem;
}}
.metric {{
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  padding: 0.5rem 0.65rem;
}}
.metric-label {{
  font-size: 0.65rem;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: #94a3b8;
  margin-bottom: 0.15rem;
  font-weight: 600;
}}
.metric-value {{
  font-size: 0.95rem;
  font-weight: 700;
  color: #0f172a;
}}
.metric-value.positive {{ color: #16a34a; }}
.metric-value.negative {{ color: #dc2626; }}
.metric-value.zoning {{ font-size: 0.8rem; }}
.solar-label {{ font-size: 0.7rem; font-weight: 500; color: #64748b; }}
.metric-value--link a {{ color: #2563eb; text-decoration: none; font-size: 0.85rem; }}
.metric-value--link a:hover {{ text-decoration: underline; }}
.walkscore-label {{ font-size: 0.65rem; font-weight: 500; color: #64748b; }}
.metric--walkscore {{ grid-column: span 2; }}
.walkscore-bar {{
  height: 4px; background: #e2e8f0; border-radius: 99px;
  margin-top: 0.35rem; overflow: hidden;
}}
.walkscore-bar__fill {{
  height: 100%; border-radius: 99px; transition: width 0.3s;
}}
.metric--transit-scores {{ grid-column: span 2; }}
.transit-score-row {{ display: flex; gap: 0.75rem; margin-top: 0.35rem; flex-wrap: wrap; }}
.score-pill {{
  flex: 1; min-width: 100px;
  font-size: 0.78rem; font-weight: 500;
}}
.score-pill--na {{ color: #94a3b8; }}
.score-bar {{
  height: 3px; background: #e2e8f0; border-radius: 99px;
  margin-top: 0.25rem; overflow: hidden;
}}
.score-bar__fill {{ height: 100%; border-radius: 99px; }}
.metric--assessed {{ grid-column: span 2; }}
.assessed-breakdown {{
  display: flex; gap: 0.75rem; margin-top: 0.3rem; flex-wrap: wrap;
}}
.assessed-breakdown span {{
  font-size: 0.72rem; color: #64748b; font-weight: 500;
  background: #f1f5f9; border-radius: 4px; padding: 0.15rem 0.4rem;
}}

/* ── Property features ── */
.features {{
  display: flex; flex-wrap: wrap; gap: 0.4rem;
}}
.feature-chip {{
  font-size: 0.72rem; font-weight: 600;
  background: #f1f5f9; color: #475569;
  border: 1px solid #e2e8f0;
  border-radius: 99px; padding: 0.2rem 0.6rem;
  white-space: nowrap;
}}

/* ── Narrative ── */
.narrative {{
  border-top: 1px solid #f1f5f9;
  padding-top: 0.875rem;
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 0.35rem;
}}
.narrative p {{
  font-size: 0.9rem;
  color: #334155;
  line-height: 1.6;
  margin: 0;
}}
.narrative p:last-child {{
  font-weight: 500;
  color: #0f172a;
}}

/* ── Verdict banner ── */
.verdict-banner {{
  margin-top: 0.5rem;
  padding: 0.5rem 0.875rem;
  border-radius: 8px;
  border: 1px solid;
  font-size: 0.85rem;
  font-weight: 600;
  letter-spacing: 0.01em;
}}

/* ── Verdict reasons ── */
.verdict-reasons {{
  margin: 0.5rem 0 0 0;
  padding: 0.625rem 0.875rem 0.625rem 1.5rem;
  background: #f8fafc;
  border-radius: 8px;
  border: 1px solid #e2e8f0;
  font-size: 0.82rem;
  color: #475569;
  line-height: 1.6;
}}
.verdict-reasons li {{
  margin-bottom: 0.2rem;
}}
.verdict-reasons li:last-child {{
  margin-bottom: 0;
}}

/* ── Price targets ── */
.price-targets {{
  margin-top: 0.75rem;
  padding: 0.75rem 0.875rem;
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
}}
.pt-title {{
  font-size: 0.78rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: #64748b;
  margin-bottom: 0.5rem;
}}
.pt-table {{
  width: 100%;
  border-collapse: collapse;
  font-size: 0.82rem;
}}
.pt-table th {{
  text-align: left;
  padding: 0.2rem 0.5rem 0.2rem 0;
  color: #94a3b8;
  font-weight: 600;
  font-size: 0.75rem;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  border-bottom: 1px solid #e2e8f0;
}}
.pt-table td {{
  padding: 0.3rem 0.5rem 0.3rem 0;
  color: #334155;
  border-bottom: 1px solid #f1f5f9;
}}
.pt-table tr:last-child td {{
  border-bottom: none;
}}
.pt-ok {{ color: #b45309; }}
.pt-hard {{ color: #dc2626; font-weight: 600; }}
.pt-note {{
  margin: 0.5rem 0 0 0;
  font-size: 0.8rem;
  line-height: 1.5;
  padding: 0.4rem 0.6rem;
  border-radius: 6px;
}}
.pt-note--ok {{
  background: #fefce8;
  color: #713f12;
  border: 1px solid #fde68a;
}}
.pt-note--hard {{
  background: #fff1f2;
  color: #9f1239;
  border: 1px solid #fecdd3;
}}
.pt-rate-label {{
  font-size: 0.75rem;
  color: #64748b;
  margin-bottom: 0.3rem;
  font-style: italic;
}}
.pt-rate-header {{
  font-size: 0.75rem;
  font-weight: 600;
  color: #475569;
  margin: 0.6rem 0 0.3rem 0;
  padding-top: 0.5rem;
  border-top: 1px solid #e2e8f0;
}}
.pt-rent-note {{
  font-size: 0.8rem;
  color: #64748b;
  margin-top: 0.5rem;
  padding: 0.3rem 0;
  border-top: 1px solid #f1f5f9;
}}

/* ── Zoning potential ── */
.zoning-potential {{
  border-top: 1px solid #f1f5f9;
  padding-top: 0.875rem;
}}
.zp-header {{
  display: flex; align-items: center; gap: 0.5rem; margin-bottom: 0.5rem;
}}
.zp-title {{
  font-size: 0.75rem; font-weight: 700; text-transform: uppercase;
  letter-spacing: 0.06em; color: #475569;
}}
.zp-score {{ font-size: 1rem; letter-spacing: 0.05em; }}
.zp-badges {{ display: flex; flex-wrap: wrap; gap: 0.35rem; margin-bottom: 0.5rem; }}
.zp-badge {{
  font-size: 0.68rem; font-weight: 600; padding: 0.15rem 0.5rem;
  border-radius: 99px; white-space: nowrap;
}}
.zp-badge--green  {{ background: #dcfce7; color: #166534; }}
.zp-badge--blue   {{ background: #dbeafe; color: #1e40af; }}
.zp-badge--purple {{ background: #f3e8ff; color: #6b21a8; }}
.zp-badge--gray   {{ background: #f1f5f9; color: #475569; }}
.zp-summary {{
  font-size: 0.8rem; color: #475569; margin-bottom: 0.4rem; line-height: 1.5;
}}
.zp-opps {{
  margin: 0; padding-left: 1rem;
  font-size: 0.775rem; color: #64748b; line-height: 1.7;
}}

/* ── Schools ── */
.schools {{
  border-top: 1px solid #f1f5f9;
  padding-top: 0.875rem;
}}
.schools-header {{
  font-size: 0.75rem; font-weight: 700; text-transform: uppercase;
  letter-spacing: 0.06em; color: #475569; margin-bottom: 0.4rem;
}}
.schools-list {{
  margin: 0; padding-left: 0; list-style: none;
  font-size: 0.775rem; color: #64748b; line-height: 1.8;
}}
.school-level {{
  font-size: 0.6rem; font-weight: 700; background: #e2e8f0; color: #475569;
  padding: 0.1rem 0.35rem; border-radius: 3px; vertical-align: middle;
  letter-spacing: 0.04em; margin-right: 0.25rem;
}}

/* ── Redfin button ── */
.redfin-btn {{
  display: inline-block;
  background: #cc2329;
  color: #fff;
  font-size: 0.8rem;
  font-weight: 600;
  padding: 0.45rem 1rem;
  border-radius: 6px;
  text-decoration: none;
  align-self: flex-start;
  transition: background 0.15s;
}}
.redfin-btn:hover {{ background: #a81c21; }}

/* ── Appreciation signals ── */
.appreciation {{
  border-top: 1px solid #f1f5f9;
  padding-top: 0.875rem;
}}
.ap-header {{
  display: flex; align-items: center; gap: 0.5rem; margin-bottom: 0.4rem;
}}
.ap-title {{
  font-size: 0.75rem; font-weight: 700; text-transform: uppercase;
  letter-spacing: 0.06em; color: #475569;
}}
.ap-score {{ font-size: 1rem; letter-spacing: 0.05em; }}
.ap-signals {{
  margin: 0; padding-left: 1rem;
  font-size: 0.775rem; color: #64748b; line-height: 1.7;
}}

/* ── Market context strip ── */
.market-snapshot {{
  border-top: 1px solid #f1f5f9;
  padding-top: 0.875rem;
  margin-top: 0.875rem;
}}
.mkt-header {{
  display: flex; align-items: baseline; gap: 0.5rem;
  flex-wrap: wrap; margin-bottom: 0.6rem;
}}
.mkt-title {{
  font-size: 0.75rem; font-weight: 700; text-transform: uppercase;
  letter-spacing: 0.06em; color: #475569;
}}
.mkt-asof {{ font-size: 0.7rem; color: #94a3b8; margin-left: auto; }}
.mkt-tiles {{
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
  gap: 0.5rem;
}}
.mkt-tile {{
  background: #f8fafc; border: 1px solid #eef2f7;
  border-radius: 8px; padding: 0.5rem 0.625rem;
}}
.mkt-tile-label {{
  font-size: 0.65rem; text-transform: uppercase; letter-spacing: 0.05em;
  color: #94a3b8; margin-bottom: 0.15rem;
}}
.mkt-tile-value {{ font-size: 0.9rem; font-weight: 700; color: #1e293b; }}
.mkt-sub {{ font-size: 0.7rem; font-weight: 500; color: #64748b; }}
.mkt-compare {{
  margin-top: 0.6rem; font-size: 0.8rem; color: #475569; line-height: 1.6;
}}
.mkt-note {{
  margin-top: 0.6rem; font-size: 0.75rem; color: #92400e;
  background: #fffbeb; border: 1px solid #fde68a;
  border-radius: 6px; padding: 0.4rem 0.55rem;
}}
.mkt-source {{ margin-top: 0.5rem; font-size: 0.68rem; color: #94a3b8; }}

/* ── Assumption sliders ── */
.sliders-bar {{
  position: sticky;
  top: 0;
  z-index: 100;
  background: #fff;
  border-bottom: 1px solid #e2e8f0;
  padding: 0.75rem 2rem;
  display: flex; flex-wrap: wrap; gap: 1.5rem; align-items: center;
  font-size: 0.875rem;
  box-shadow: 0 1px 4px rgba(0,0,0,0.06);
}}
.sliders-bar label {{ color: #374151; white-space: nowrap; display: flex; align-items: center; gap: 0.5rem; font-weight: 500; }}
.sliders-bar input[type=range] {{ width: 120px; accent-color: #2563eb; vertical-align: middle; }}
.sliders-bar select {{
  background: #f8fafc; color: #1e293b; border: 1px solid #e2e8f0;
  border-radius: 6px; padding: 0.25rem 0.5rem; font-size: 0.875rem;
}}
.slider-val {{ font-weight: 700; color: #2563eb; min-width: 3rem; display: inline-block; }}
.sliders-bar .recalc-note {{
  font-size: 0.75rem; color: #94a3b8; margin-left: auto;
}}

/* ── Map ── */
.map-section {{
  max-width: none;
  margin: 1.25rem 0 0;
  padding: 0 2rem;
}}
#property-map {{
  height: 380px;
  border-radius: 12px;
  border: 1px solid #e2e8f0;
  overflow: hidden;
  box-shadow: 0 1px 3px rgba(0,0,0,0.06);
}}

/* ── Comparison table ── */
.comparison-table-wrap {{
  max-width: none;
  margin: 1.25rem 0 0;
  padding: 0 2rem;
}}
.ct-header {{
  display: flex; align-items: baseline; gap: 0.5rem; margin-bottom: 0.5rem;
}}
.ct-title {{
  font-size: 0.75rem; font-weight: 700; text-transform: uppercase;
  letter-spacing: 0.06em; color: #475569;
}}
.ct-note {{ font-size: 0.72rem; color: #94a3b8; }}
.ct-scroll {{ overflow-x: auto; border-radius: 10px; }}
.comparison-table {{
  width: 100%; border-collapse: collapse; font-size: 0.82rem;
  background: #fff; border: 1px solid #e2e8f0; border-radius: 10px; overflow: hidden;
}}
.comparison-table th {{
  text-align: left; padding: 0.5rem 0.75rem;
  background: #f8fafc; color: #64748b; font-weight: 600;
  font-size: 0.68rem; text-transform: uppercase; letter-spacing: 0.05em;
  border-bottom: 1px solid #e2e8f0; white-space: nowrap;
}}
.comparison-table td {{
  padding: 0.45rem 0.75rem; border-bottom: 1px solid #f1f5f9; vertical-align: middle;
}}
.comparison-table tr:last-child td {{ border-bottom: none; }}
.comparison-table tr:hover td {{ background: #f8fafc; }}
.ct-rank {{ font-weight: 800; color: #2563eb; width: 1.5rem; }}
.ct-address {{ font-weight: 600; color: #0f172a; }}
.ct-price {{ font-weight: 600; white-space: nowrap; }}
.ct-bedbath {{ color: #64748b; white-space: nowrap; }}
.ct-cap {{ font-weight: 700; color: #0369a1; white-space: nowrap; }}
.ct-piti {{ font-weight: 700; color: #475569; white-space: nowrap; }}
.ct-cf {{ font-weight: 700; white-space: nowrap; }}
.ct-cf.positive {{ color: #16a34a; }}
.ct-cf.negative {{ color: #dc2626; }}
.ct-ws {{ color: #64748b; text-align: center; }}
.verdict-chip {{
  font-size: 0.68rem; font-weight: 600; padding: 0.15rem 0.5rem;
  border-radius: 99px; white-space: nowrap; display: inline-block;
}}
@media (max-width: 700px) {{
  .comparison-table-wrap {{ padding: 0 1rem; }}
}}

/* ── Responsive ── */
@media (max-width: 700px) {{
  .card {{ grid-template-columns: 1fr; }}
  .card-photo {{ height: 180px; }}
  .metrics-grid {{ grid-template-columns: 1fr 1fr; }}
  .main {{ padding: 1rem; }}
  .sliders-bar {{ padding: 0.75rem 1rem; gap: 0.75rem; }}
  .sliders-bar .recalc-note {{ display: none; }}
  #property-map {{ height: 260px; }}
  .map-section {{ padding: 0 1rem; }}
}}
</style>
{"<link rel='stylesheet' href='https://unpkg.com/leaflet@1.9.4/dist/leaflet.css' />" if has_map else ""}
{"<script src='https://unpkg.com/leaflet@1.9.4/dist/leaflet.js'></script>" if has_map else ""}
</head>
<body>

<div class="page-header">
  <h1>{page_title} &nbsp;·&nbsp; {shortlist.market}</h1>
  <p class="subtitle">{subtitle}</p>
  <div class="summary-bar">{shortlist.run_summary}</div>
</div>

{sliders_html}

{map_section}
{comparison_table}
<main class="main">
{cards_html}
</main>

<script>
(function() {{
  var CLOSING_COST_PCT = 0.03;

  function calcMortgage(price, downPct, annualRate, termYears) {{
    var loan = price * (1 - downPct);
    var monthlyRate = annualRate / 12;
    var n = termYears * 12;
    if (monthlyRate < 0.000001) return loan / n;
    return loan * (monthlyRate * Math.pow(1 + monthlyRate, n)) /
           (Math.pow(1 + monthlyRate, n) - 1);
  }}

  function fmtCashflow(val) {{
    var sign = val >= 0 ? '+' : '';
    return sign + '$' + Math.abs(Math.round(val)).toLocaleString() + '/mo';
  }}

  function fmtPct(val) {{
    return (val * 100).toFixed(2) + '%';
  }}

  window.applyShowFilter = function() {{
    var sel = document.getElementById('selectShow');
    if (!sel) return;
    var n = parseInt(sel.value);
    document.querySelectorAll('.card[data-rank]').forEach(function(card) {{
      var rank = parseInt(card.dataset.rank);
      card.style.display = (n === 0 || rank <= n) ? '' : 'none';
    }});
  }};

  window.recalcAll = function() {{
    var downPct = parseFloat(document.getElementById('sliderDown').value) / 100;
    var rate    = parseFloat(document.getElementById('sliderRate').value) / 100;
    var term    = parseInt(document.getElementById('selectTerm').value);

    document.querySelectorAll('.card[data-noi-annual]').forEach(function(card) {{
      var price = parseFloat(card.dataset.price);
      var noi   = parseFloat(card.dataset.noiAnnual);
      if (!price || isNaN(noi)) return;

      var newMortgage  = calcMortgage(price, downPct, rate, term);
      var newCashflow  = noi / 12 - newMortgage;
      var totalCash    = price * downPct + price * CLOSING_COST_PCT;
      var newCoc       = totalCash > 0 ? (newCashflow * 12) / totalCash : 0;

      var cfEl = card.querySelector('.js-cashflow');
      var cocEl = card.querySelector('.js-coc');
      if (cfEl) {{
        cfEl.textContent = fmtCashflow(newCashflow);
        cfEl.className = 'metric-value js-cashflow ' + (newCashflow >= 0 ? 'positive' : 'negative');
      }}
      if (cocEl) {{
        cocEl.textContent = fmtPct(newCoc);
        cocEl.className = 'metric-value js-coc ' + (newCoc >= 0 ? 'positive' : 'negative');
      }}
    }});
  }};

  // Apply "show top N" filter on initial page load
  applyShowFilter();
}})();
</script>
</body>
</html>"""


# ── CLI entry point ────────────────────────────────────────────────────────────

def _load_shortlist(path: Path) -> Shortlist:
    data = json.loads(path.read_text(encoding="utf-8"))
    return Shortlist.model_validate(data)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate HTML report from shortlist JSON")
    parser.add_argument("input", help="Path to shortlist JSON")
    parser.add_argument("--output", help="Output HTML path (default: same name, .html)")
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"Error: file not found: {input_path}", file=sys.stderr)
        sys.exit(1)

    output_path = Path(args.output) if args.output else input_path.with_suffix(".html")
    shortlist = _load_shortlist(input_path)
    generate_report(shortlist, output_path)
    print(f"Report written to {output_path}")
