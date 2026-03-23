# Real Estate Deal Scout

An agentic pipeline that surfaces high-conviction real estate investment opportunities. It fetches listings, screens them against your criteria, runs financial analysis, flags risks, and uses Claude to rank and narrate the top deals.

```
fetch → screen → enrich → analyze → flag risks → rank + narrate
```

## Features

- **Screening** — filters by price, beds, days on market, and optional HOA fee cap
- **Financial analysis** — cap rate, cash-on-cash return, monthly cash flow, NOI
- **Neighborhood enrichment** — Walk Score (via API or fixture data)
- **Risk flagging** — FEMA flood zones, DOM outliers
- **Rich listing data** — bedrooms, bathrooms, sqft, lot size, home type, school district, HOA
- **Claude-powered narration** — ranked shortlist with a written investment thesis per deal
- **Mock mode** — runs the full pipeline without an API key for development/testing

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Copy `.env.example` to `.env` and add your keys (optional — mock mode works without them):

```bash
ANTHROPIC_API_KEY=your-key-here
WALKSCORE_API_KEY=your-key-here   # optional
```

## Usage

**Run with Claude (requires `ANTHROPIC_API_KEY`):**

```bash
python scout.py
```

**Run in mock mode (no API key needed):**

Set `use_mock_ranker: true` in `config.yaml`, then:

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
criteria:
  max_price: 500000       # maximum purchase price
  min_beds: 3             # minimum bedrooms
  max_dom: 30             # maximum days on market
  target_cap_rate: 0.05   # target cap rate for ranking

financial_assumptions:
  down_payment_pct: 0.25
  loan_rate_annual: 0.07
  loan_term_years: 30
  vacancy_rate: 0.08
  management_fee_pct: 0.10

output:
  max_shortlist: 5
  market: "Seattle, WA"
  use_mock_ranker: false  # set true to skip Claude API call
```

## Pipeline Stages

| Stage | Input | Output |
|-------|-------|--------|
| Fetch | market name | `list[RawListing]` |
| Screen | listings + criteria | filtered listings |
| Enrich | listings | listings + Walk Score |
| Analyze | enriched listings | cap rate, CoC, cash flow |
| Flag risks | analyzed listings | flood zone + DOM flags |
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

60 tests covering screening logic, financial formulas, enrichment (mocked), risk flagging, and full pipeline integration.

## Project Structure

```
.
├── config.yaml          # investment criteria and assumptions
├── pipeline.py          # main orchestrator
├── scout.py             # CLI entry point
├── fixtures/
│   └── listings.json    # sample Seattle WA listings (20 properties)
├── tools/
│   ├── models.py        # Pydantic models for all pipeline I/O
│   ├── fetch.py         # listing loader
│   ├── screen.py        # screening logic
│   ├── enrich.py        # Walk Score enrichment
│   ├── analyze.py       # financial calculations
│   ├── risks.py         # flood zone + DOM risk flagging
│   └── mock_ranker.py   # heuristic ranker (no API key required)
└── tests/               # pytest test suite
```

## Roadmap

See [TODOS.md](TODOS.md) for planned improvements, including real MLS/Zillow data ingestion, rent comps API integration, and concurrent enrichment.
