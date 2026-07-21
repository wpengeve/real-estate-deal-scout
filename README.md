# Real Estate Deal Scout

An agentic pipeline that surfaces high-conviction real estate investment opportunities. Describe what you're looking for in plain English, or configure your criteria in YAML — the pipeline fetches listings, screens them, enriches with neighborhood data, runs personalized financial analysis, flags risks, and uses Claude to rank and narrate the top deals.

```
fetch → screen → enrich → analyze → flag risks → rank + narrate
```

📐 **New here?** Read the [Architecture & Code Guide](ARCHITECTURE.md) for a plain-English tour of how the whole thing fits together (there's also a [browser version](ARCHITECTURE.html) with a glossary).

**Vision:** Evolving from a personal CLI tool into a multi-user real estate investment agent — any user describes their financial situation and goals and gets a personalized ranked shortlist with AI narratives computed for *their* specific numbers.

## Features

- **Conversational intake** — `--chat` mode: describe your criteria in plain English; Claude extracts your investment config
- **Screening** — filters by price, beds, days on market, HOA fee, home type, walk score, cap rate, and city
- **Financial analysis** — cap rate, cash-on-cash return, monthly cash flow, NOI, mortgage payment (all personalized to your down payment and rate)
- **HUD Fair Market Rents** — auto-detected by ZIP code via USPS crosswalk; 1.0× multiplier validated against Zillow (Apr 2026)
- **Walk Score** — color-coded walkability display; fallback link when API key is not set
- **School data** — nearby schools with NCES proficiency scores and walking distance
- **Solar potential** — NREL GHI solar resource data (peak sun hours/day), cached per location
- **KC Assessor enrichment** — zoning code, tax assessed value (land + improvement)
- **Zoning analysis** — ADU/DADU eligibility, WA HB 1110 duplex rights, subdivision potential, development score 1–5
- **Appreciation signals** — price-to-rent ratio (GRM), land value %, assessment ratio, renovation candidate flag
- **Risk flagging** — FEMA flood zones, DOM outliers, HOA exposure, low cap rate
- **Claude-powered narration** — ranked shortlist with a written investment thesis per deal
- **Multiple rankers** — Claude API, Ollama (local LLM), or mock (fully offline)
- **Interactive HTML report** — live financial sliders, Leaflet map pins, school data, solar, Walk Score

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Copy `.env.example` to `.env` and add your keys:

```bash
ANTHROPIC_API_KEY=your-key-here   # required for --chat mode and ranker: claude
HUD_API_KEY=your-key-here         # free at huduser.gov — required for rent estimates
NREL_API_KEY=your-key-here        # free at developer.nrel.gov — required for solar data
WALKSCORE_API_KEY=your-key-here   # optional, free at walkscore.com
```

Mock and Ollama modes work without `ANTHROPIC_API_KEY`. HUD and NREL keys are free.

## Usage

**Chat mode — describe your criteria in plain English:**

```bash
python scout.py --chat
```

Claude will ask what you're looking for, extract your criteria (budget, cities, beds, down payment, etc.), confirm back, then run the full pipeline.

**Standard run (criteria from config.yaml):**

```bash
python scout.py
```

**Override market or shortlist size:**

```bash
python scout.py --market "Portland, OR" --max-shortlist 10
```

**Re-run narration from saved analyzed data (skips slow enrichment):**

```bash
python scout.py --from-analyzed outputs/20260322-123456_analyzed.json
```

**Run fully offline (mock ranker, no API keys needed):**

Set `ranker: mock` in `config.yaml`, then `python scout.py`.

## Configuration

Edit `config.yaml` to tune your investment criteria:

```yaml
fetch:
  data_source: csv             # "fixtures" | "csv" | "redfin"
  csv_paths:                   # combine multiple Redfin CSV exports
    - data/redfin_2026-03-27.csv

enrich:
  hud_rent_multiplier: 1.0    # validated against Zillow Apr 2026 for Seattle

criteria:
  max_price: 2250000
  min_beds: 3
  max_dom: 9999
  target_cap_rate: 0.05
  min_cap_rate: 0.02           # realistic floor for Seattle market
  max_hoa_fee: 0               # 0 = no HOA allowed
  preferred_home_types:
    - "Single Family"
  allowed_cities:              # ~20-mile radius; null = no filter
    - "Seattle"
    - "Bellevue"
    - "Kirkland"

financial_assumptions:
  down_payment_pct: 0.25
  loan_rate_annual: 0.0525
  loan_term_years: 30
  vacancy_rate: 0.08
  management_fee_pct: 0.10

output:
  max_shortlist: 15
  market: "Seattle, WA"
  ranker: ollama               # "mock" | "ollama" | "claude"
  ollama_model: llama3.1:8b
```

## Pipeline Stages

| Stage | Input | Output |
|-------|-------|--------|
| Fetch | config | `list[RawListing]` |
| Screen | listings + criteria | filtered listings (price, beds, DOM, HOA, city, home type) |
| Enrich | listings | + HUD rent, Walk Score, schools, solar GHI, KC Assessor data |
| Analyze | enriched listings | cap rate, CoC, cash flow, zoning potential, appreciation signals |
| Flag risks | analyzed listings | flood zone, DOM outliers, HOA, cap rate flags |
| Rank + narrate | flagged listings | `Shortlist` with AI-written investment narratives |

## Financial Formulas

- **Cap rate** = NOI / purchase price (financing-independent)
- **NOI** = effective rent − management − maintenance − insurance − property tax
- **Effective rent** = gross rent × (1 − vacancy rate)
- **CoC return** = annual cash flow / total cash invested
- **Monthly cash flow** = effective rent − mortgage − monthly expenses
- **Total cash invested** = down payment + closing costs

Seattle market context: SFH cap rates typically run 2–4%. Negative cashflow is common — most Seattle investment thesis is appreciation + ADU/zoning upside.

## Running Tests

```bash
pytest
```

217 tests covering screening logic, financial formulas, enrichment (mocked), risk flagging, zoning, appreciation signals, school lookups, solar data, conversational intake, and full pipeline integration.

## Project Structure

```
.
├── config.yaml              # investment criteria and assumptions
├── pipeline.py              # main orchestrator
├── scout.py                 # CLI entry point (--chat, --from-analyzed, --market)
├── data/
│   └── redfin_*.csv         # Redfin CSV exports (real Seattle listings)
├── fixtures/
│   └── listings.json        # sample listings (fixture / offline mode)
├── outputs/                 # pipeline run outputs (JSON + HTML reports)
└── tools/
    ├── models.py            # Pydantic models for all pipeline I/O
    ├── fetch.py             # listing loader (fixtures, CSV, Redfin API)
    ├── screen.py            # screening logic (price, beds, DOM, HOA, city)
    ├── enrich.py            # HUD rent, Walk Score, schools, solar, assessor
    ├── analyze.py           # financial calculations
    ├── risks.py             # risk flagging
    ├── zoning_potential.py  # ADU/DADU/HB1110 zoning analysis
    ├── appreciation.py      # appreciation signal scoring
    ├── schools.py           # nearby school lookup (NCES EDGE + Urban Institute)
    ├── solar.py             # NREL solar resource API (GHI, cached)
    ├── assessor.py          # KC Assessor tax data
    ├── crosswalk.py         # USPS ZIP → county FIPS crosswalk (HUD + Census)
    ├── chat_intake.py       # conversational criteria extraction (Claude tool-use)
    ├── report.py            # interactive HTML report generator
    ├── mock_ranker.py       # heuristic ranker (no API key required)
    └── ollama_ranker.py     # local LLM ranker
```

## Roadmap

See [TODOS.md](TODOS.md) for the full platform roadmap.

**Current focus (Phase 1):**
- Live listings API evaluation (Rentcast / ATTOM / RapidAPI) — unblocks multi-market
- FastAPI wrapper to serve the pipeline as a web endpoint

**Later phases:**
- User accounts + saved searches + email alerts
- Multi-market support beyond Seattle
- Feedback loop (thumbs up/down → improves rankings)