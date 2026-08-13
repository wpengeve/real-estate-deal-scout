# Real Estate Deal Scout

An agentic pipeline that surfaces high-conviction real estate investment opportunities. Describe what you're looking for in plain English, or configure your criteria in YAML — the pipeline fetches listings, screens them, enriches with neighborhood data, runs personalized financial analysis, flags risks, and uses Claude to rank and narrate the top deals.

```
fetch → screen → enrich → analyze → flag risks → rank + narrate
```

📐 **New here?** Read the [Architecture & Code Guide](ARCHITECTURE.md) for a plain-English tour of how the whole thing fits together (there's also a [browser version](ARCHITECTURE.html) with a glossary).

**Vision:** Evolving from a personal CLI tool into a multi-user real estate investment agent — any user describes their financial situation and goals and gets a personalized ranked shortlist with AI narratives computed for *their* specific numbers.

📋 **Where things stand:** [TODOS.md](TODOS.md) opens with a *Status at a glance* summary — what
works today, what was built most recently, what's decided, and what's next. Start there to catch
up without reading code.

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
- **Area market context** — months of supply, sale-to-list, % sold above asking, days to contract, and this home's $/sqft vs the city median (free Redfin data, no API key); the AI ranker sees it too
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

**Refresh area-market data (optional, monthly):**

```bash
python scout.py --market-refresh WA
```

Adds a "Market Context" strip to each deal in the report — months of supply,
sale-to-list ratio, share sold above asking, median days to contract, and how the
home's $/sqft compares to its city median. The AI ranker sees the same numbers, so
narratives can weigh pricing against the city and expected competition.

Source is the free [Redfin Data Center](https://www.redfin.com/news/data-center/)
city market tracker: no API key, no quota. The download is ~950 MB and the upstream
file is unsorted by region, so this is a batch refresh, not a per-search fetch. The
filtered slice lands in `data/market_trends_<STATE>.tsv` (gitignored).

Re-running is cheap: it checks the upstream `Last-Modified` first and skips the
download when Redfin hasn't published anything new (`--force` overrides). Redfin's
monthly files publish on an irregular schedule, so this is safe to run whenever.

The download is streamed and never stored — a full refresh peaks at ~61 MB of memory,
takes under a minute, and leaves a ~7 MB slice. It runs comfortably on any host size.
Set `MARKET_TRENDS_DIR` to keep the slice on a persistent volume so it survives
redeploys; it defaults to `data/`.

Reports work fine without it; the strip simply doesn't appear until you refresh.
Note the data is monthly and typically runs 1–2 months behind, and percentages are
hidden for cities with fewer than 10 recorded sales in the period.

## Configuration

Edit `config.yaml` to tune your investment criteria:

```yaml
fetch:
  data_source: scraperapi      # "fixtures" | "csv" | "redfin" | "scraperapi" | "rentcast"
  csv_paths:                   # only used when data_source: csv
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
  ranker: claude               # "mock" | "ollama" | "claude"
  ollama_model: llama3.1:8b
```

> **How it works** — the pipeline stages, financial formulas, file layout, and design
> decisions all live in the **[Architecture & Code Guide](ARCHITECTURE.md)**. This README
> stays focused on installing and running the tool.

## Running Tests

```bash
pytest
```

434 tests covering screening logic, financial formulas, enrichment (mocked), risk flagging, zoning, appreciation signals, school lookups, solar data, area market context, conversational intake, and full pipeline integration.

> For the file-by-file layout, see the [Architecture & Code Guide](ARCHITECTURE.md#3-directory-map--what-each-file-does).

## Roadmap

See [TODOS.md](TODOS.md) for the full platform roadmap.

- **Phase 1 — "anyone can use it": essentially done.** Live listings (Rentcast), plain-English intake, and the FastAPI web app all shipped; remaining: deploy to a public host.
- **Phase 2 — "multi-market + accounts": mostly done.** Login, user accounts, and saved report history already exist.
- **Phase 3 — "retention": not started.** Auto re-run saved searches, email alerts, and a thumbs up/down feedback loop.