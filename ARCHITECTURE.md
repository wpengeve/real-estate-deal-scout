# Real Estate Deal Scout — Architecture & Code Guide

> A section-by-section tour of the codebase **and** an explanation of *how* it is
> built — the design decisions, patterns, and data flow that make up the base of
> the project. Read this to understand the foundation before diving into any file.

---

## 1. What this project is (in one paragraph)

Real Estate Deal Scout is an **agentic pipeline** that surfaces high-conviction
real estate investment deals. You describe what you want (in plain English or via
`config.yaml`); the system fetches live listings, screens them against your
criteria, enriches each with neighborhood data (rent, schools, solar, zoning, tax
assessment), runs **personalized** financial analysis (cap rate, cash-on-cash,
cash flow computed for *your* down payment and rate), flags risks, and finally
uses an LLM to **rank and narrate** the top deals. It ships both as a **CLI tool**
(`scout.py`) and a **web app** (`app.py`, FastAPI).

The single most important idea: **Python does the math, the LLM only ranks and
explains.** Every number a user sees comes from deterministic code — the LLM never
invents financials.

---

## 2. The mental model — one pipeline, six stages

Everything in the project revolves around this flow:

```
fetch → screen → enrich → analyze → flag risks → rank + narrate
```

| # | Stage | Input | Output | Where |
|---|-------|-------|--------|-------|
| 1 | **Fetch** | config | `list[RawListing]` | `tools/fetch.py` |
| 2 | **Screen** | listings + criteria | filtered listings | `tools/screen.py` |
| 3 | **Enrich** | listings | + rent, walk score, schools, solar, tax/zoning | `tools/enrich.py` |
| 4 | **Analyze** | enriched | cap rate, CoC, cash flow, zoning & appreciation | `tools/analyze.py` |
| 5 | **Flag risks** | analyzed | flood, seismic, wildfire, DOM outlier flags | `tools/risks.py` |
| 6 | **Rank + narrate** | flagged | `Shortlist` with written thesis per deal | `tools/*_ranker.py` |

Stages 1–5 are **deterministic Python**. Stage 6 is the only place an LLM (or a
heuristic fallback) is involved, and even then only for *ordering and prose*.

`pipeline.py` is the orchestrator that wires these six stages together.

---

## 3. Directory map — what each file does

### Top-level

| File | Role |
|------|------|
| `scout.py` | **CLI entry point.** Parses args (`--chat`, `--from-analyzed`, `--market`), loads config, runs the pipeline, prints a `rich` shortlist to the terminal. |
| `app.py` | **Web entry point (FastAPI).** Chat UI, magic-link auth, background pipeline runs, HTML report serving. |
| `pipeline.py` | **The orchestrator.** `run()` executes all six stages; also `run_from_analyzed()`, `run_single_property()`, `run_multi_property()`. Handles URL auto-resolution, progress callbacks, saving outputs. |
| `db.py` | **SQLite persistence** (SQLAlchemy) — users, auth tokens, sessions, chat sessions, report runs. |
| `config.yaml` | Investment criteria, financial assumptions, data source, ranker choice. |
| `.env` / `.env.example` | API keys (all read from env, never hardcoded). |
| `README.md` | User-facing setup & usage. |
| `CLAUDE.md` | **TPM policy** — autonomous rules for data-source and API decisions. |
| `ROLES.md` | Who owns which decisions (CEO vs. delegated engineering/design/QA). |
| `PROGRESS.md` / `TODOS.md` | Personal progress log and roadmap. |

### `tools/` — the building blocks

**The contract:**
- `models.py` — **Pydantic schemas for every stage's I/O.** This is the spine of
  the whole project; every other module depends on these types. Key models:
  `InvestmentConfig` (all config), `RawListing` (the canonical listing),
  `EnrichResult`, `AnalyzedListing`, `FlaggedListing`, `DealNarrative` (flattened
  per-deal output), and `Shortlist` (the final result).

**Stage 1 — Fetch (listing acquisition):**
- `fetch.py` — Largest module. Four backends: **fixtures**, **Redfin CSV**,
  **ScraperAPI** (Redfin proxy), **Rentcast** (live API). Handles city/address →
  Redfin URL resolution with Brave/DuckDuckGo fallbacks. File-based response cache
  to conserve paid API credits.
- `scraperapi_normalizer.py` — Regex-parses ScraperAPI's semi-structured Redfin
  JSON into typed `RawListing`s.
- `geocode.py` — Google Maps geocoding (address → lat/lon), cached.
- `single_property.py` — "Analyze one specific property" flow; direct Redfin fetch
  with ScraperAPI fallback.

**Stage 2 — Screen:**
- `screen.py` — Pure filtering against `ScreeningCriteria`. Returns machine-readable
  reason codes (`price_too_high`, `beds_below_min`, `city_not_in_target_area`, …).

**Stage 3 — Enrich (neighborhood data):**
- `enrich.py` — Orchestrator; concurrently fans out per-listing enrichment.
- `crosswalk.py` — ZIP → county (HUD USPS crosswalk + Census FIPS lookup).
- `assessor.py` — County assessor tax/zoning data (King, Snohomish, Pierce via ArcGIS).
- `schools.py` — Nearby schools (NCES EDGE) + proficiency scores (WA OSPI).
- `solar.py` — NREL solar irradiance (GHI → peak sun hours), cached.
- `redfin_scraper.py` — Parses a Redfin listing page for features (primary suite,
  garage, basement, walk/bike/transit scores) via pure regex.

**Stage 4 — Analyze:**
- `analyze.py` — Pure financial math: amortization, NOI, cap rate, CoC, cash flow,
  PITI (for primary-residence mode). Formulas documented in the docstring.
- `zoning_potential.py` — Rule-based development upside (ADU/DADU, WA HB 1110 duplex
  rights, subdivision), score 1–5. Hardcoded Seattle/KC zone tables.
- `appreciation.py` — Appreciation signals (price-to-rent/GRM, assessment ratio,
  land-value %, renovation flag), score 1–5. No I/O — derives from existing data.

**Stage 5 — Flag risks:**
- `risks.py` — FEMA flood zones, USGS seismic (PGA), wildfire risk class, DOM
  outliers. Also folds in zoning & appreciation into the `FlaggedListing`.

**Stage 6 — Rank + narrate:**
- `mock_ranker.py` — Fully offline heuristic ranker (weighted cap rate + CoC − risk).
  Default; needs no API key.
- `ollama_ranker.py` — Local LLM (llama3.1:8b) via Ollama. Free, lower quality.
- (Claude ranking lives in `pipeline.py` / the chat modules — `ranker: claude`.)

**Conversational intake & output:**
- `chat_intake.py` — CLI `--chat`: Claude tool-use turns plain English into an
  `InvestmentConfig`.
- `web_chat.py` — Async, session-stateful version for the web UI; also powers
  post-results Q&A ("compare deal #1 and #3").
- `report.py` — Generates the self-contained interactive **HTML report** (Leaflet
  maps, live financial sliders, schools, solar, comparison table).

### Other directories
- `tests/` — ~217 tests (pytest), one file per tool module + pipeline integration.
- `fixtures/` — Sample listings for offline mode.
- `data/` — Redfin CSV exports + `scout.db` (SQLite).
- `outputs/` — Per-run JSON + HTML reports (auto-pruned after 7 days).
- `templates/` — Jinja2 templates for the web UI.

---

## 4. How the code is written — the design principles

These are the patterns that recur everywhere. Understanding these five ideas is
understanding the base of the project.

### 4.1 Pydantic models are the contract
Every stage takes a typed model in and returns a typed model out (`models.py`).
This means data shape is validated at every boundary, config errors are caught
early with clear messages, and you can serialize any intermediate result to JSON
(that's how `--from-analyzed` re-runs ranking without re-fetching).

### 4.2 Graceful degradation is non-negotiable
Every external API call **returns `None`/empty and logs a warning rather than
raising**. A missing API key never crashes the pipeline — you just get partial
enrichment (some fields `None`). This is codified as policy in `CLAUDE.md`. It's
why the tool works end-to-end even with zero paid keys.

### 4.3 The LLM never touches the numbers
The rankers (mock, ollama, claude) receive already-computed financials and are
asked only to **order the deals and write prose**. Hallucinated addresses are
rejected; all cap rates, cash flows, and metrics come from `analyze.py`. This is
the project's core integrity guarantee.

### 4.4 Aggressive caching to protect API budgets
Two layers: (a) **in-process dict caches** (geocode, crosswalk, solar, schools, HUD
data) for the life of a run; (b) a **file-based TTL cache** (`data/scraperapi_cache/`)
for paid ScraperAPI/Rentcast responses so re-runs don't burn credits.

### 4.5 Data-sourcing preference order (from `CLAUDE.md`)
When new data is needed, the order is: **already-in-a-page-we-fetch → free public
API → free-tier-with-key → paid (needs approval).** Paid dependencies are flagged
with a `TODO`, never integrated autonomously. This keeps running costs near zero
by design.

### 4.6 Async where it pays, sync where it doesn't
The enrichment stack is **async** (`httpx.AsyncClient`, `asyncio.gather`,
semaphores, a shared client) because it fans out many network calls per listing.
Risk lookups are **sync** — a deliberate MVP simplification, not an oversight.

---

## 5. The two entry points

### CLI — `scout.py`
```bash
python scout.py                 # run from config.yaml
python scout.py --chat          # describe criteria in plain English
python scout.py --market "Portland, OR" --max-shortlist 10
python scout.py --from-analyzed outputs/…_analyzed.json   # skip slow enrichment
```
Loads/validates config → (optional chat intake) → `pipeline.run()` → prints a
`rich` panel per deal (price, cap rate, cash flow, zoning stars, narrative).

### Web — `app.py` (FastAPI)
A full web application, more built-out than the roadmap docs suggest:
- **Chat UI** (`/`) backed by `web_chat.ChatSession`
- **Magic-link auth** — `/api/auth/request` → token → `/auth/verify` → session cookie
- **Background pipeline runs** — `POST /api/run` starts a run, `GET /api/run/{id}`
  polls status; progress streamed via a callback
- **Report serving** — `GET /reports/{run_id}` returns the generated HTML
- **History** — logged-in users see past runs (`/history`)

State: in-memory dicts for live runs + chat sessions; **SQLite (`db.py`) for
durable** users, sessions, and report-run records. Reports persist on disk and are
pruned after 7 days on startup.

---

## 6. External data sources

| Source | What it provides | Key needed | Cost |
|--------|------------------|-----------|------|
| Redfin (CSV / scrape) | Listings, page features, walk score | none | free |
| ScraperAPI | Redfin proxy (avoids blocks) | `SCRAPERAPI_KEY` | paid |
| Rentcast | Live for-sale listings (multi-market) | `RENTCAST_API_KEY` | free tier |
| HUD FMR | Fair Market Rent estimates by county | `HUD_API_KEY` | free |
| HUD USPS crosswalk + Census | ZIP → county → FIPS | `HUD_API_KEY` | free |
| County ArcGIS (KC/Snohomish/Pierce) | Tax assessment, zoning | none | free |
| NCES EDGE + WA OSPI | Schools + proficiency | none | free |
| NREL | Solar irradiance (GHI) | `NREL_API_KEY` | free |
| Walk Score | Walkability | `WALKSCORE_API_KEY` | free tier |
| Google Maps | Geocoding | `GOOGLE_MAPS_KEY` | free tier |
| FEMA / USGS / ArcGIS | Flood, seismic, wildfire | none | free |
| Anthropic | Chat intake + Claude ranking | `ANTHROPIC_API_KEY` | paid |
| Ollama | Local LLM ranking | none (local) | free |

Only **ScraperAPI** and **Anthropic** cost money; everything else is free or has a
free tier, and all degrade gracefully when absent.

---

## 7. Configuration & ranking

`config.yaml` drives a run without touching code. Key knobs:
- `fetch.data_source` — `fixtures | csv | redfin | scraperapi | rentcast`
- `criteria.*` — price, beds, DOM, HOA, home types, `allowed_cities` radius filter
- `financial_assumptions.*` — down payment %, loan rate, vacancy, mgmt fee, etc.
  (these personalize every metric)
- `output.ranker` — `mock` (offline default) | `ollama` (local) | `claude` (best)
- `purpose` — `rental` (cap rate / CoC math) vs `primary` (PITI-only)

**Ranker trade-off:** mock is instant and free but crude; ollama is free but ranks
poorly; claude is the quality target (needs the API key — the current top TODO).

---

## 8. Testing

`pytest`, ~217 tests, one file per tool plus `test_pipeline.py` for integration.
External APIs are mocked. Per `ROLES.md`, QA is always-on: the full suite must pass
before every commit, and new code carries its own tests.

---

## 9. Where the project is heading

- **Phase 1 (current):** anyone can use it. Live-listings API (Rentcast) ✓,
  chat intake ✓, web wrapper ✓. 
- **Phase 2:** multi-market + accounts. Auth & DB schema already exist in `db.py`.
- **Phase 3:** retention — background re-runs of saved searches, email alerts,
  a thumbs-up/down feedback loop into ranking.

See `TODOS.md` for the full roadmap and `PROGRESS.md` for the session log.

---

*Generated as a codebase orientation guide. For the authoritative behavior, the
code and `models.py` are the source of truth.*
