# Real Estate Deal Scout — Architecture & Code Guide

> A section-by-section tour of the codebase **and** an explanation of *how* it is
> built — the design decisions, patterns, and data flow that make up the base of
> the project. Read this to understand the foundation before diving into any file.

**How to read this doc:** Sections 1–2 give you the big picture in plain language
(start here). Section 3 maps every file. Section 4 explains the design decisions.
Sections 5–8 are reference. **Section 10 is a glossary** — if any term below is
unfamiliar, jump there first.

---

## 1. What this project is (in one paragraph)

Real Estate Deal Scout finds good real estate investment deals for you and explains
*why* they're good. You tell it what you're looking for (in plain English, or in a
settings file); it pulls real listings off the market, throws out the ones that
don't fit, looks up extra facts about each remaining home (likely rent, nearby
schools, sunlight, zoning, tax value), does the investment math **using your own
budget and loan terms**, flags anything risky, and finally has an AI write a short
ranked pitch for the best few. It runs two ways: from the command line
(`scout.py`) or as a website (`app.py`).

The single most important idea: **the Python code does all the math; the AI only
ranks the results and writes the explanations.** Every number you see is computed
by code — the AI never makes up a figure.

---

## 2. The mental model — one pipeline, six stages

"Pipeline" just means an assembly line: data enters one end, passes through a fixed
sequence of steps, and comes out the other end as a finished result. Everything in
the project revolves around this line:

```
fetch → screen → enrich → analyze → flag risks → rank + narrate
```

| # | Stage | What goes in | What comes out | Where |
|---|-------|--------------|----------------|-------|
| 1 | **Fetch** | your settings | a list of raw listings | `tools/fetch.py` |
| 2 | **Screen** | listings + your criteria | only the listings that qualify | `tools/screen.py` |
| 3 | **Enrich** | qualifying listings | same listings + extra data (rent, schools, solar, tax/zoning) | `tools/enrich.py` |
| 4 | **Analyze** | enriched listings | + the investment math (cap rate, cash flow, upside) | `tools/analyze.py` |
| 5 | **Flag risks** | analyzed listings | + warnings (flood, earthquake, wildfire, stale listing) | `tools/risks.py` |
| 6 | **Rank + narrate** | flagged listings | the final ranked shortlist, each with a written pitch | `tools/*_ranker.py` |

Stages 1–5 are **plain, predictable Python** — same input always gives the same
output. Stage 6 is the only step that uses AI, and even then only to *decide the
order and write the words*, never to compute numbers.

`pipeline.py` is the "factory manager" that runs these six stages in order.

### A concrete example — follow one house through the line

Say you tell it: *"3-bed rentals under $900k in Seattle, 25% down."*

1. **Fetch** pulls ~200 Seattle listings from Rentcast.
2. **Screen** drops anything over $900k, under 3 beds, or outside Seattle → ~40 left.
3. **Enrich** looks up, for each of the 40: likely monthly rent (HUD), nearby schools
   and their test scores, rooftop sun hours (NREL), zoning + tax value (county records).
4. **Analyze** computes, *using your 25% down*: this house rents for ~$3,200/mo, so
   its cap rate is 4.1%, cash-on-cash is 6.3%, cash flow is +$180/mo.
5. **Flag risks** notices the house sits in a FEMA flood zone → adds a MEDIUM risk flag.
6. **Rank + narrate** sorts the 40, and the AI writes: *"#1 — strong 4.1% cap rate and
   rare positive cash flow for Seattle; note the flood-zone insurance cost."*

That flow is the whole product.

---

## 3. Directory map — what each file does

### Top-level

| File | Role |
|------|------|
| `scout.py` | **Command-line entry point.** Reads your options, runs the pipeline, prints the results to the terminal. |
| `app.py` | **Website entry point (FastAPI).** Chat interface, login, running the pipeline in the background, showing reports. |
| `pipeline.py` | **The orchestrator** — runs the six stages in order. Also has variants for re-running old results and analyzing a single property. |
| `db.py` | **Database code** (SQLite) — stores users, logins, sessions, and past report runs. |
| `config.yaml` | Your settings: what to search for, your budget/loan terms, which AI to use. **Untracked** — copy `config.yaml.example` to create it, then edit freely. |
| `config.yaml.example` | The tracked template `config.yaml` is copied from, documented inline. |
| `.env` / `.env.example` | Secret API keys (read from the environment, never written into code). |
| `README.md` | Setup & usage instructions. |
| `CLAUDE.md` | Rules the AI assistant follows when adding new data sources (see §4.5). |
| `ROLES.md` | Who owns which decisions on the project. |
| `TODOS.md` | Roadmap and current status — the authoritative one, kept current. |

### `tools/` — the building blocks

**The shared vocabulary:**
- `config_file.py` — Finds and reads `config.yaml`. Both `scout.py` and `app.py` go
  through it, so the CLI and the website can't disagree about where settings live or
  what to say when the file is missing.
- `models.py` — **The definitions of every kind of data the pipeline passes around**
  (a "listing," an "analyzed deal," the final "shortlist," etc.). Think of it as the
  project's dictionary — every other file agrees on these shapes. This is the single
  most important file to read first. Key ones: `InvestmentConfig` (all your settings),
  `RawListing` (one home), `Shortlist` (the final ranked result).

**Stage 1 — Fetch (getting listings):**
- `fetch.py` — The biggest file. Gets listings from any of four sources: **fixtures**
  (fake sample data for testing), **Redfin CSV** (a downloaded file), **ScraperAPI**
  (pulls live Redfin data), or **Rentcast** (a live listings API).
- `scraperapi_normalizer.py` — Cleans up the messy data ScraperAPI returns into a tidy `RawListing`.
- `geocode.py` — Turns a street address into map coordinates (via Google Maps).
- `single_property.py` — The "just analyze this one house" path (from a Redfin link).

**Stage 2 — Screen (filtering):**
- `screen.py` — Keeps only listings that match your criteria, and records *why* each
  reject was dropped (`price_too_high`, `beds_below_min`, …).

**Stage 3 — Enrich (looking up extra facts):**
- `enrich.py` — Coordinates all the lookups below for each listing, at the same time.
- `crosswalk.py` — Figures out which county a ZIP code belongs to.
- `assessor.py` — County tax records: assessed value + zoning (King/Snohomish/Pierce).
- `schools.py` — Nearby schools and their test-score ratings.
- `solar.py` — How much sunlight the roof gets (for solar potential).
- `redfin_scraper.py` — Reads a Redfin listing page for extras (primary suite, garage,
  basement, walkability scores).

**Stage 4 — Analyze (the money math):**
- `analyze.py` — All the investment calculations (rent, mortgage, cap rate, cash flow).
  The formulas are written out at the top of the file.
- `zoning_potential.py` — Can you add a backyard cottage, split the lot, or build a
  duplex? Scores the development upside 1–5.
- `appreciation.py` — Signals that a home might rise in value (underpriced vs. rent,
  under-assessed, a fixer-upper). Scores it 1–5.

**Stage 5 — Flag risks:**
- `risks.py` — Adds warnings: FEMA flood zone, earthquake shaking, wildfire risk, and
  "listing has sat unusually long." Also attaches the zoning & appreciation scores.

**Stage 6 — Rank + narrate:**
- `mock_ranker.py` — Ranks with a simple formula, no AI. The free, offline default.
- `ollama_ranker.py` — Ranks with a free AI model running on your own computer.
- Claude ranking (the best option) lives in `pipeline.py`, turned on with `ranker: claude`.

**Area market context (not a stage — a lookup both the report and the ranker use):**
- `market_trends.py` — How a *city's* market is behaving (months of supply, sale-to-list,
  share sold above asking, median days to contract), from the free Redfin Data Center
  file. Because that file is ~950 MB and unsorted by region, this is a **batch refresh**
  (`scout.py --market-refresh WA`) that filters one state to a small local slice, not a
  per-search fetch. Lookups then read the slice, and degrade to "no context" — never an
  error — when no refresh has been run.

**Chat & output:**
- `chat_intake.py` — The command-line chat: turns "3-bed rentals under $900k" into settings.
- `web_chat.py` — The website version of that chat; also answers follow-up questions
  about the results ("compare deal #1 and #3").
- `report.py` — Builds the interactive **HTML report** (maps, sliders, schools, solar).

### Other folders
- `tests/` — the automated tests (see §8).
- `fixtures/` — fake sample listings for offline testing.
- `data/` — downloaded listing files, the `scout.db` database, and the area-market
  slice built by `--market-refresh` (all gitignored; the slice is regenerable).
- `outputs/` — the results of each run (auto-deleted after 7 days).
- `templates/` — the HTML page templates for the website.

---

## 4. How the code is written — the design principles

These six ideas recur everywhere. Understanding them *is* understanding the base of
the project.

### 4.1 One agreed-upon shape for every piece of data
Every stage receives a well-defined data object and returns another (defined in
`models.py`, using a library called **Pydantic**). Because the shape is checked at
every step, bad data or a typo in your settings is caught immediately with a clear
error — instead of crashing halfway through. It also means any intermediate result
can be saved to a file and reloaded later (that's how "re-run without re-fetching" works).

### 4.2 Never crash on missing data
Every call to an outside service is written so that if it fails or a key is missing,
it **quietly returns "nothing" and logs a note** instead of stopping the program.
Worst case, a home is missing its school rating — the run still finishes. This is why
the tool works even with zero paid API keys.

### 4.3 The AI never touches the numbers
The rankers get the already-computed figures and are asked only to **put the deals in
order and write the explanation**. If the AI invents an address that isn't in the
data, it's thrown out. Every dollar figure comes from `analyze.py`, not the AI. This
is the project's core trust guarantee.

### 4.4 Remember answers to avoid repeat lookups (caching)
The tool saves the answers it gets so it doesn't ask twice: short-term memory during a
single run (map coordinates, county lookups, sunlight), plus a saved-to-disk cache for
the *paid* services so re-running doesn't cost money again.

### 4.5 A ranked preference for where data comes from (from `CLAUDE.md`)
When new data is needed, the order is: **use something we already downloaded → a free
public source → a free source that needs a key → a paid service (only with approval).**
This keeps running costs near zero on purpose.

### 4.6 Do many lookups at once, but keep it simple where it doesn't matter
The enrichment stage fires off many web lookups **simultaneously** (that's what
"async" means) because each home needs several, and waiting for them one-by-one would
be slow. The risk lookups are done the simple one-at-a-time way — a deliberate
shortcut, not an oversight.

---

## 5. The two entry points

### Command line — `scout.py`
```bash
python scout.py                 # run using the settings in config.yaml
python scout.py --chat          # describe what you want in plain English
python scout.py --market "Portland, OR" --max-shortlist 10
python scout.py --from-analyzed outputs/…_analyzed.json   # reuse saved data, skip the slow lookups
```
It loads your settings → (optional chat) → runs the pipeline → prints one nicely
formatted card per deal in the terminal.

### Website — `app.py` (FastAPI)
A full web app:
- **Chat interface** at `/` — describe what you want in the browser
- **Login** by email "magic link" (you get a link, clicking it logs you in — no password)
- **Background runs** — the site kicks off a pipeline run and shows a live progress bar
- **Reports** — each finished run gets its own shareable page (`/reports/{id}`)
- **History** — logged-in users can see their past runs

Live runs and chats are held in memory; users, logins, and run history are saved in a
**SQLite database** (`db.py`). Report pages are kept on disk and cleaned up after 7 days.

---

## 6. External data sources

Only **ScraperAPI** and **Anthropic** cost money; everything else is free or has a
free tier, and every one degrades gracefully if its key is missing.

| Source | What it provides | Key needed | Cost |
|--------|------------------|-----------|------|
| Redfin (CSV / scrape) | Listings, page features, walk score | none | free |
| ScraperAPI | Pulls live Redfin data (avoids being blocked) | `SCRAPERAPI_KEY` | paid |
| Rentcast | Live for-sale listings (any US market) | `RENTCAST_API_KEY` | free tier |
| HUD FMR | Typical rent estimates by county | `HUD_API_KEY` | free |
| HUD crosswalk + Census | ZIP → county lookup | `HUD_API_KEY` | free |
| County records (KC/Snohomish/Pierce) | Tax value, zoning | none | free |
| NCES + WA OSPI | Schools + test scores | none | free |
| NREL | Rooftop sunlight | `NREL_API_KEY` | free |
| Walk Score | Walkability | `WALKSCORE_API_KEY` | free tier |
| Google Maps | Address → coordinates | `GOOGLE_MAPS_KEY` | free tier |
| FEMA / USGS | Flood, earthquake, wildfire | none | free |
| Anthropic (Claude) | Chat + ranking | `ANTHROPIC_API_KEY` | paid |
| Ollama | AI ranking on your own computer | none (local) | free |

---

## 7. Configuration & ranking

`config.yaml` controls a run without touching any code. It is untracked — copy
`config.yaml.example` to create it — so your budget and loan terms stay yours and
never show up as a pending git change. `tools/config_file.py` is the only thing that
locates and reads it, shared by both entry points. The key settings:
- `fetch.data_source` — where listings come from (`fixtures | csv | redfin | scraperapi | rentcast`)
- `criteria.*` — price, beds, HOA, home types, which cities count
- `financial_assumptions.*` — your down payment %, loan rate, vacancy, etc.
  (these are what make every number personal to *you*)
- `output.ranker` — which ranker: `mock` (offline) / `ollama` (local AI) / `claude` (best)
- `purpose` — `rental` (investment math) vs `primary` (you'll live there, so mortgage-only)

**Ranker trade-off:** `mock` is instant and free but crude; `ollama` is free but
ranks poorly; `claude` is the quality option (needs the paid key — and is what the
project runs today).

---

## 8. Testing

The project has **442 automated tests** (run with `pytest`) — roughly one test file
per building block, plus an end-to-end test of the whole pipeline. Outside services
are faked during tests so they run fast and offline. Policy: the full test suite must
pass before every commit, and any new code brings its own tests.

One convention worth copying when you fix a bug here: **write the test first and
confirm it fails against the unfixed code.** Two separate reviews of the market-context
work found defects sitting in code the suite already "covered", and one round of fixes
shipped with tests that called the fixed function directly — one layer below the bug —
so the suite stayed green while the CLI kept the old behaviour. A regression test that
was never seen to fail proves nothing.

---

## 9. Where the project is heading

- **Phase 1 — "anyone can use it": essentially done.** Live listings (Rentcast) ✓,
  plain-English intake ✓, website ✓. Remaining: deploy it to a public host.
- **Phase 2 — "multi-market + accounts": mostly done.** Login, user accounts, and
  saved report history already exist in `db.py`.
- **Phase 3 — "keep users coming back": not started.** Auto re-run saved searches,
  email alerts for new matches, and a thumbs-up/down feedback loop.

See `TODOS.md` for the full roadmap — it is the authoritative status doc and is kept
current. (`PROGRESS.md` is a personal session log, gitignored, so a fresh clone won't
have one.)

---

## 10. Glossary (plain English)

**Real-estate / finance**
- **Cap rate** — a home's yearly rental profit (before mortgage) divided by its price.
  Higher = better return. Seattle homes typically run a low 2–4%.
- **Cash-on-cash (CoC)** — yearly cash profit divided by the actual cash you put in
  (down payment + closing costs). Measures return on *your* money.
- **Cash flow** — money left over each month after rent pays the mortgage and expenses.
  Positive = the property pays you; negative = you feed it.
- **NOI (net operating income)** — yearly rent minus running costs (management,
  maintenance, insurance, taxes), *not* counting the mortgage.
- **PITI** — a mortgage payment's four parts: Principal, Interest, Taxes, Insurance.
  Used in "primary residence" mode (when you'll live there, not rent it out).
- **GRM (gross rent multiplier)** — price divided by yearly rent; a quick "is this
  expensive relative to rent?" gauge. Also called price-to-rent.
- **DOM (days on market)** — how long a listing has been for sale. A very high number
  can signal a problem (or an opportunity). In the market-context strip it means
  something narrower: the median days *until a home went under contract*, which stops
  at the accepted offer, not the closing (add ~30–45 days for that).
- **Sale-to-list** — the average ratio of a home's final sale price to its final list
  price. 1.01 means homes sold ~1% over asking. Note *final*: a home cut from $1M to
  $900k and sold at $900k scores 1.000, so this understates seller weakness — which is
  why the report always shows price drops next to it.
- **Months of supply** — how long it would take to sell every home currently for sale
  at the current pace. Low = a seller's market, high = a buyer's market.
- **ADU / DADU** — a second small home on the same lot: an Accessory Dwelling Unit
  (e.g. a basement apartment) or *Detached* ADU (a backyard cottage). Extra rental income.
- **HB 1110** — a 2023 Washington law letting more lots build duplexes/multiplexes.
- **Zoning** — the local rules for what can be built on a lot. Determines ADU/duplex upside.
- **Assessed value** — the county's official value of a property for tax purposes.

**Technical**
- **LLM / AI model** — a large language model like Claude; here it only ranks and writes.
- **Pipeline** — a fixed sequence of processing steps (the six stages).
- **Pydantic** — a Python library that defines and validates data shapes (see §4.1).
- **Async** — doing many slow web lookups at the same time instead of one after another.
- **Cache** — remembered answers, so the same lookup isn't repeated (see §4.4).
- **Geocoding** — converting a street address into map coordinates (latitude/longitude).
- **FIPS** — a standard numeric code identifying a US county.
- **GHI** — Global Horizontal Irradiance: how much sunlight hits a spot (solar potential).
- **FastAPI / SQLite** — the web-server framework and the small on-disk database.
- **Magic link** — passwordless login: you get an emailed link that logs you in.
- **Fixtures** — canned sample data used for testing without hitting the real internet.

---

*Generated as a codebase orientation guide. For the authoritative behavior, the
code and `models.py` are the source of truth.*
