# Real Estate Deal Scout

An agentic pipeline that surfaces high-conviction real estate investment opportunities. It fetches listings, screens them against your criteria, enriches with neighborhood and school data, runs financial analysis, flags risks, and uses Claude to rank and narrate the top deals.

```
fetch → screen → enrich → analyze → flag risks → rank + narrate
```

## Features

- **Screening** — filters by price, beds, days on market, HOA fee, home type, walk score, and cap rate
- **Financial analysis** — cap rate, cash-on-cash return, monthly cash flow, NOI, mortgage payment
- **Neighborhood enrichment** — Walk Score, HUD Fair Market Rents (auto-detected by ZIP)
- **School data** — nearby schools with NCES proficiency scores and distance
- **Zoning analysis** — ADU/DADU eligibility, HB 1110 duplex rights, subdivision potential
- **Appreciation signals** — price-to-rent ratio, land value %, assessment ratio, renovation candidate flag
- **Risk flagging** — FEMA flood zones, DOM outliers, HOA exposure, low cap rate
- **Claude-powered narration** — ranked shortlist with a written investment thesis per deal
- **Multiple rankers** — Claude API, Ollama (local), or mock (no API key needed)
- **HTML report** — interactive output with financials, map pins, and school data

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Copy `.env.example` to `.env` and add your keys (optional — mock/Ollama modes work without them):

```bash
ANTHROPIC_API_KEY=your-key-here   # for ranker: claude
WALKSCORE_API_KEY=your-key-here   # optional, free at walkscore.com
HUD_API_KEY=your-key-here         # free at huduser.gov
```

## Usage

**Run with Ollama (default, no API key needed):**

```bash
python scout.py
```

**Run with Claude (requires `ANTHROPIC_API_KEY`):**

Set `ranker: claude` in `config.yaml`, then:

```bash
python scout.py
```

**Run in mock mode (fully offline):**

Set `ranker: mock` in `config.yaml`, then:

```bash
python scout.py
```

**Override market or shortlist size:**

```bash
python scout.py --market "Denver, CO" --max-shortlist 3
```

**Re-run narration from saved analyzed data:**

```bash
python scout.py --from-analyzed outputs/20260322-123456_analyzed.json
```

## Configuration

Edit `config.yaml` to tune your investment criteria:

```yaml
fetch:
  data_source: csv             # "fixtures" | "csv" | "redfin"
  csv_path: data/redfin.csv

criteria:
  max_price: 2250000
  min_beds: 3
  max_dom: 9999
  target_cap_rate: 0.05
  walkscore_min: 50
  max_hoa_fee: 0               # 0 = no HOA allowed
  preferred_home_types:
    - "Single Family"

financial_assumptions:
  down_payment_pct: 0.25
  loan_rate_annual: 0.0525
  loan_term_years: 30
  vacancy_rate: 0.08
  management_fee_pct: 0.10

output:
  max_shortlist: 5
  market: "Seattle, WA"
  ranker: ollama               # "mock" | "ollama" | "claude"
  ollama_model: llama3.1:8b
```

## Pipeline Stages

| Stage | Input | Output |
|-------|-------|--------|
| Fetch | config | `list[RawListing]` |
| Screen | listings + criteria | filtered listings |
| Enrich | listings | + Walk Score, HUD rent estimate |
| Analyze | enriched listings | cap rate, CoC, cash flow, zoning, appreciation |
| Flag risks | analyzed listings | flood zone, DOM, HOA, cap rate flags |
| Rank + narrate | flagged listings | `Shortlist` with narratives |

## Financial Formulas

- **Cap rate** = NOI / purchase price
- **NOI** = effective rent − management − maintenance − insurance − property tax
- **Effective rent** = gross rent × (1 − vacancy rate)
- **CoC return** = annual cash flow / total cash invested
- **Monthly cash flow** = effective rent − mortgage − monthly expenses
- **Total cash invested** = down payment + closing costs

## Running Tests

```bash
pytest
```

189 tests covering screening logic, financial formulas, enrichment (mocked), risk flagging, zoning, appreciation signals, school lookups, and full pipeline integration.

## Project Structure

```
.
├── config.yaml              # investment criteria and assumptions
├── pipeline.py              # main orchestrator
├── scout.py                 # CLI entry point
├── fixtures/
│   └── listings.json        # sample Seattle WA listings (fixture data)
├── data/
│   └── redfin.csv           # Redfin CSV export (real listings)
├── tools/
│   ├── models.py            # Pydantic models for all pipeline I/O
│   ├── fetch.py             # listing loader (fixtures, CSV, Redfin API)
│   ├── screen.py            # screening logic
│   ├── enrich.py            # Walk Score + HUD rent enrichment
│   ├── analyze.py           # financial calculations
│   ├── risks.py             # risk flagging
│   ├── zoning_potential.py  # ADU/DADU/HB1110 zoning analysis
│   ├── appreciation.py      # appreciation signal scoring
│   ├── schools.py           # nearby school lookup (NCES data)
│   ├── assessor.py          # tax assessment parsing
│   ├── crosswalk.py         # USPS ZIP → county FIPS crosswalk
│   ├── report.py            # HTML report generator
│   ├── mock_ranker.py       # heuristic ranker (no API key required)
│   └── ollama_ranker.py     # local LLM ranker
└── tests/                   # pytest test suite
```

## Roadmap

See [TODOS.md](TODOS.md) for planned improvements.