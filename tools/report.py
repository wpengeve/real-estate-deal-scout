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
from pathlib import Path

from tools.models import DealNarrative, FinancialAssumptions, Shortlist

_RISK_COLOR = {
    "LOW":    {"bg": "#dcfce7", "text": "#166534", "border": "#86efac"},
    "MEDIUM": {"bg": "#fef9c3", "text": "#854d0e", "border": "#fde047"},
    "HIGH":   {"bg": "#fee2e2", "text": "#991b1b", "border": "#fca5a5"},
}


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


# ── Map helpers ────────────────────────────────────────────────────────────────

def _map_div(deal: DealNarrative, idx: int) -> str:
    """Return a Leaflet map div + initialization script for one property."""
    if deal.latitude is None or deal.longitude is None:
        # Show a Google Maps search link as fallback
        query = deal.address.replace(" ", "+")
        return f"""<div class="map-placeholder">
          <a href="https://www.google.com/maps/search/?api=1&query={query}"
             target="_blank" rel="noopener" class="map-link">📍 View on Google Maps</a>
        </div>"""

    map_id = f"map-{idx}"
    lat, lng = deal.latitude, deal.longitude
    gmaps = f"https://www.google.com/maps?q={lat},{lng}&layer=c&cbll={lat},{lng}"
    return f"""<div id="{map_id}" class="map-container"></div>
<script>
(function() {{
  var m = L.map('{map_id}', {{zoomControl: false, dragging: false, scrollWheelZoom: false}})
           .setView([{lat}, {lng}], 15);
  L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
    attribution: '© <a href="https://openstreetmap.org">OSM</a>',
    maxZoom: 19
  }}).addTo(m);
  L.marker([{lat}, {lng}]).addTo(m);
  document.getElementById('{map_id}').addEventListener('click', function() {{
    window.open('{gmaps}', '_blank');
  }});
}})();
</script>"""


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
        items = "".join(f"<li>{o}</li>" for o in zp.opportunities)
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


def _render_deal(deal: DealNarrative, idx: int) -> str:
    risk = _RISK_COLOR.get(deal.risk_level, _RISK_COLOR["LOW"])
    cf_class = _cashflow_class(deal.monthly_cashflow)

    beds = f"{deal.beds} bd" if deal.beds else "—"
    baths = f"{deal.baths} ba" if deal.baths else "—"
    dom = f"{deal.days_on_market} days on market" if deal.days_on_market is not None else "—"
    sqft = f"{deal.sqft:,} sqft" if deal.sqft else "—"
    lot = f"{deal.lot_sqft:,} sqft lot" if deal.lot_sqft else ""
    year = f"Built {deal.year_built}" if deal.year_built else ""

    redfin_btn = ""
    if deal.listing_url:
        redfin_btn = f'<a href="{deal.listing_url}" target="_blank" rel="noopener" class="redfin-btn">View on Redfin →</a>'

    map_html = _map_div(deal, idx)

    # data attributes for JS recalculation
    noi_attr = f' data-noi-annual="{deal.noi_annual}"' if deal.noi_annual is not None else ""
    price_attr = f' data-price="{deal.price}"' if deal.price else ""

    return f"""
<div class="card{'  card--first' if deal.rank == 1 else ''}"{price_attr}{noi_attr}>
  <div class="card-map">{map_html}</div>
  <div class="card-body">

    <div class="card-header">
      <div class="rank-badge">#{deal.rank}</div>
      <div class="card-title-block">
        <div class="card-address">{deal.address}</div>
        <div class="card-meta">{beds} &nbsp;/&nbsp; {baths} &nbsp;·&nbsp; {sqft}
          {f"&nbsp;·&nbsp; {lot}" if lot else ""}
          {f"&nbsp;·&nbsp; {year}" if year else ""}
          &nbsp;·&nbsp; {dom}
        </div>
      </div>
      <div class="risk-badge" style="background:{risk['bg']};color:{risk['text']};border-color:{risk['border']}">
        {deal.risk_level}
      </div>
    </div>

    <div class="metrics-grid">
      <div class="metric">
        <div class="metric-label">Price</div>
        <div class="metric-value">{_fmt_currency(deal.price)}</div>
      </div>
      <div class="metric">
        <div class="metric-label">Cap Rate</div>
        <div class="metric-value">{_fmt_pct(deal.cap_rate)}</div>
      </div>
      <div class="metric">
        <div class="metric-label">Cash-on-Cash</div>
        <div class="metric-value js-coc">{_fmt_pct(deal.coc_return)}</div>
      </div>
      <div class="metric">
        <div class="metric-label">Monthly Cash Flow</div>
        <div class="metric-value js-cashflow {cf_class}">{_fmt_cashflow(deal.monthly_cashflow)}</div>
      </div>
      {_render_assessed(deal)}
      {f'<div class="metric"><div class="metric-label">Walk Score</div><div class="metric-value">{deal.walk_score}</div></div>' if deal.walk_score is not None else ""}
      {f'<div class="metric"><div class="metric-label">Flood Zone</div><div class="metric-value">{deal.flood_zone}</div></div>' if deal.flood_zone else ""}
      {f'<div class="metric"><div class="metric-label">Zoning</div><div class="metric-value zoning">{deal.zoning}</div></div>' if deal.zoning else ""}
      {f'<div class="metric"><div class="metric-label">HOA</div><div class="metric-value negative">{_fmt_currency(deal.hoa_fee)}/mo</div></div>' if deal.hoa_fee else ""}
      {f'<div class="metric"><div class="metric-label">Home Type</div><div class="metric-value">{deal.home_type}</div></div>' if deal.home_type else ""}
    </div>

    <div class="narrative">{deal.narrative}</div>

    {_render_zoning_potential(deal)}
    {_render_appreciation(deal)}

    {redfin_btn}
  </div>
</div>"""


# ── Full page ──────────────────────────────────────────────────────────────────

def _render(shortlist: Shortlist, assumptions: FinancialAssumptions | None = None) -> str:
    cards_html = "\n".join(_render_deal(d, i) for i, d in enumerate(shortlist.deals))
    count = len(shortlist.deals)

    # Slider defaults from assumptions (or fallbacks)
    default_down = int((assumptions.down_payment_pct if assumptions else 0.25) * 100)
    default_rate = (assumptions.loan_rate_annual if assumptions else 0.07) * 100
    default_term = assumptions.loan_term_years if assumptions else 30

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Deal Scout — {shortlist.market}</title>

<!-- Leaflet.js for maps (OpenStreetMap, no API key) -->
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>

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
.main {{ max-width: 1100px; margin: 0 auto; padding: 2rem 1.5rem; }}

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

/* ── Map pane ── */
.card-map {{
  position: relative;
  min-height: 200px;
  background: #e2e8f0;
  cursor: pointer;
}}
.map-container {{
  width: 100%;
  height: 100%;
  min-height: 200px;
}}
.map-placeholder {{
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100%;
  min-height: 200px;
}}
.map-link {{
  color: #2563eb;
  text-decoration: none;
  font-size: 0.875rem;
  font-weight: 500;
}}
.map-link:hover {{ text-decoration: underline; }}

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
.metric--assessed {{ grid-column: span 2; }}
.assessed-breakdown {{
  display: flex; gap: 0.75rem; margin-top: 0.3rem; flex-wrap: wrap;
}}
.assessed-breakdown span {{
  font-size: 0.72rem; color: #64748b; font-weight: 500;
  background: #f1f5f9; border-radius: 4px; padding: 0.15rem 0.4rem;
}}

/* ── Narrative ── */
.narrative {{
  font-size: 1rem;
  color: #334155;
  line-height: 1.7;
  border-top: 1px solid #f1f5f9;
  padding-top: 0.875rem;
  flex: 1;
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

/* ── Responsive ── */
@media (max-width: 700px) {{
  .card {{ grid-template-columns: 1fr; }}
  .card-map {{ min-height: 180px; }}
  .map-container {{ min-height: 180px; }}
  .metrics-grid {{ grid-template-columns: 1fr 1fr; }}
  .main {{ padding: 1rem; }}
  .sliders-bar {{ padding: 0.75rem 1rem; gap: 0.75rem; }}
  .sliders-bar .recalc-note {{ display: none; }}
}}
</style>
</head>
<body>

<div class="page-header">
  <h1>Deal Scout &nbsp;·&nbsp; {shortlist.market}</h1>
  <p class="subtitle">Top {count} investment {'property' if count == 1 else 'properties'} &nbsp;·&nbsp; Click a map to open Street View</p>
  <div class="summary-bar">{shortlist.run_summary}</div>
</div>

<div class="sliders-bar">
  <label>
    Down payment:
    <input type="range" id="sliderDown" min="5" max="50" step="1" value="{default_down}"
           oninput="document.getElementById('valDown').textContent=this.value+'%';recalcAll()">
    <span class="slider-val" id="valDown">{default_down}%</span>
  </label>
  <label>
    Loan rate:
    <input type="range" id="sliderRate" min="3" max="12" step="0.25" value="{default_rate:.2f}"
           oninput="document.getElementById('valRate').textContent=parseFloat(this.value).toFixed(2)+'%';recalcAll()">
    <span class="slider-val" id="valRate">{default_rate:.2f}%</span>
  </label>
  <label>
    Term:
    <select id="selectTerm" onchange="recalcAll()">
      <option value="15" {'selected' if default_term == 15 else ''}>15 yr</option>
      <option value="20" {'selected' if default_term == 20 else ''}>20 yr</option>
      <option value="30" {'selected' if default_term == 30 else ''}>30 yr</option>
    </select>
  </label>
  <span class="recalc-note">Cash flow &amp; CoC update live · cap rate is unaffected by financing</span>
</div>

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
