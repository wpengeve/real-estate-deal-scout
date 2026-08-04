# TODOS

Last updated: 2026-07-21

---

## Done ✓

### Data & enrichment
- **Live listings** — ScraperAPI (Redfin proxy) + **Rentcast** (multi-market) backends, with response caching to conserve credits
- **Single-property analysis** — paste a Redfin URL (or plain address, auto-resolved) to analyze one property
- **HUD Fair Market Rents** — ZIP→county crosswalk, 1.0× multiplier (validated vs Zillow Apr 2026)
- **County assessor enrichment** — King / Snohomish / Pierce zoning + tax assessed value
- **Zoning potential** — ADU/DADU eligibility, HB 1110 duplex rights, development score 1–5
- **Appreciation signals** — price-to-rent (GRM), assessment ratio, land value %, renovation flag
- **School data** — NCES EDGE locations + WA OSPI proficiency scores
- **Solar enrichment** — NREL Solar Resource API, cached per location
- **Redfin page scraper** — primary suite, garage, basement, fireplace, walk/bike/transit scores

### Analysis & ranking
- **Rental vs primary-residence mode** — cap rate/CoC for rentals, PITI for owner-occupiers
- **Multi-property comparison** — side-by-side table, risk flags, price targets
- **Claude ranker LIVE** — `ANTHROPIC_API_KEY` added, `ranker: claude` in config (was the long-standing P1)
- **Mock + Ollama rankers** — offline and local-LLM fallbacks

### Web platform
- **FastAPI web server** (`app.py`) — browser chat intake, background runs + progress polling
- **SQLite accounts** (`db.py`) — magic-link auth, user sessions, report history
- **Chat persistence** — history saved; follow-up Q&A in context of results
- **Interactive HTML report** — Leaflet maps, live financial sliders, comparison table, schools, solar, photos

### Infra / docs
- **All API keys present** — Anthropic, Google Maps, HUD, NREL, Rentcast, ScraperAPI, Walk Score
- **369 tests passing**
- **Architecture docs** — `ARCHITECTURE.md` + `ARCHITECTURE.html`

---

## Active

### P1 — Deployment story
**What:** The FastAPI app runs locally; decide how it's hosted (Render/Fly/etc.) and
where reports live long-term (currently disk, pruned after 7 days).
**Why:** "Share a URL" (the Phase 1 goal) needs a persistent public host.
**Effort:** M.

### P2 — Tighter listing scope
**What:** Scope ScraperAPI/Rentcast queries more tightly to target cities to cut junk
(far-out suburbs still slip through before the city filter).
**Why:** Fewer junk listings = faster pipeline, cleaner results, fewer API credits.
**Effort:** S.

### P3 — School district name for real listings
**What:** `school_district` field is null for scraped listings (no good free source).
**Why:** It's a useful buyer signal; nearby-schools + proficiency already work, but the
named district doesn't populate.
**Effort:** S–M (depends on finding a source).

---

## Platform Roadmap

**Vision:** Multi-user real estate investment agent — any user describes their
financial situation and goals; the platform finds and explains the best-matching
deals for *their* specific profile.

**Core moat:** Personalized financial analysis — every cap rate, CoC, and cashflow
computed for *your* down payment and rate, then explained by AI.

### Phase 1 — "Anyone can use it" — ✓ essentially complete
- [x] Live listings API — ScraperAPI + Rentcast
- [x] Chat intake — `--chat` (CLI) and web chat
- [x] Web wrapper — FastAPI `app.py` with `/api/run` + report serving
- [ ] Public deployment (see Active P1)

### Phase 2 — "Multi-market + accounts" — mostly done
- [x] SQLite schema — users, sessions, auth tokens, report_runs, chat_sessions
- [x] Auth — email magic link
- [x] Shareable report URLs — `/reports/{run_id}` (disk-backed; TTL 7 days)
- [x] Multi-market enrichment — HUD rent + schools work nationally; assessor is WA-only
- [ ] Broaden assessor coverage beyond WA (King/Snohomish/Pierce today)

### Phase 3 — "Retention + alerts" — not started
- [ ] Background workers — re-run saved searches daily (RQ or Celery)
- [ ] Email alerts — new matching listings → digest (SendGrid/Resend)
- [ ] Feedback loop — thumbs up/down on rankings → stored for future ranking context
- [ ] Saved searches — persist criteria per user (schema groundwork exists)

### New pillar — Market Intelligence (proposed 2026-07-28)

**Idea:** Alongside per-property deal scouting, add an **area-level market-trends
dashboard** — e.g. for Seattle / Kirkland / Redmond: % of homes sold above vs. below
list price, sale-to-list ratio, median days-on-market, inventory, price trend over time.
Answers the buyer's *"is this a hot or cooling market?"* question that sits right next
to *"is this house a good deal?"*.

**Why it belongs in this repo (not a separate project):** same customer, same moment,
same web app to host it. Complementary to (not overlapping with) the per-property flow.

**Competitive reality check (2026-07-30) — this should reshape the MVP.**
The data source is Redfin's own public dashboard data, and **Redfin already publishes it as a
free public dashboard.** Anyone can look up Seattle's sale-to-list and median DOM today without
us. So a standalone `/market` page largely rebuilds something that already exists, from the same
source, for free — it is the *least* defensible part of this pillar, not the core of it.

What Redfin cannot show is the join between area stats and *the user's specific deal and
financials* — which is exactly the stated moat:

> "This home is priced 8% above Seattle's median $/sqft, and 31% of homes here sell over ask —
> at your 20% down and 6.5% rate, an over-ask offer pushes cap rate below your 5.5% floor."

**Implication:** the in-report market snapshot strip is the real product and should be built
first; the standalone dashboard is probably not worth building at all. Reorder the checklist
accordingly if this is accepted — it is currently listed as "Later".

**Data source — VERIFIED 2026-07-30. Use Redfin Data Center city market tracker.**

Source: `https://redfin-public-data.s3.us-west-2.amazonaws.com/redfin_market_tracker/city_market_tracker.tsv000.gz`
(954 MB gzipped, TSV, no API key, no quota, no rate limit)

Every metric the pillar needs is present, with MoM and YoY deltas for each:
`AVG_SALE_TO_LIST`, `SOLD_ABOVE_LIST`, `MEDIAN_DOM`, `INVENTORY`, `MONTHS_OF_SUPPLY`,
`HOMES_SOLD`, `NEW_LISTINGS`, `PENDING_SALES`, `PRICE_DROPS`, `OFF_MARKET_IN_TWO_WEEKS`,
`MEDIAN_SALE_PRICE`, `MEDIAN_LIST_PRICE`, `MEDIAN_PPSF`.

Confirmed against live data:
- **Coverage** — 555 WA cities. Seattle, Bellevue, Kirkland, Redmond all populated.
  (Seattle May 2026: sale-to-list 1.008, 30.9% above list, 11 median DOM, 799 sold, 2111 inventory.)
- **Granularity** — city × month × property type. Property types include
  `Multi-Family (2-4 Unit)`, so rental-investor cuts are possible, not just `All Residential`.
- **Clean shape** — one row per (city, period, property type). No seasonally-adjusted
  duplicates in this file (`IS_SEASONALLY_ADJUSTED` is always false), all periods 30-day.
- **Recency — softer than it first looked. Re-verify before building.** File stamped
  2026-06-02, latest period 2026-05-31. Re-checked 2026-08-03: **still 2026-06-02, identical
  byte count — no refresh in two months**, so the data is now ~2 months stale and the "updated
  monthly" cadence is unconfirmed for this S3 path. Either Redfin's cadence is slower than its
  dashboards imply, or the refreshed file lives at a different path. **Resolve this before
  building a refresh job** — a monthly cron against a file that updates irregularly is wasted
  work, and the UI needs an honest "data as of <period>" line either way. Fine for
  "hot or cooling"; never promise current-month data.

**Design constraint — rows are unsorted by region.** Extracting one city means scanning the
whole 954 MB file, so this must be a **cached monthly batch refresh, never a per-request fetch**.
Filtering WA to 2024+ yields 30,782 rows / 15 MB — small enough to persist locally
(SQLite table in `scout.db`, or a filtered TSV alongside the existing cache pattern).

**Rentcast — rejected.** `/v1/markets` returned HTTP 403 on the current key (likely paid-tier
gated). Not worth chasing: its free tier is 50 calls/month and is already spent on listing
fetches, whereas Redfin costs nothing and has the sale-to-list and above-list fields Rentcast
may not expose at all (its market data is listing-derived, not closed-transaction).

**Open question — licensing.** Redfin's Data Center, Downloads, and Methodology pages carry
no explicit license, attribution, or redistribution terms; the files are public and unauthenticated,
and the site-wide Terms of Use are the only governing document found. Attribute Redfin as the
source on any page that displays these numbers, and resolve terms with `econdata@redfin.com`
before this goes public-facing. Not a blocker for local/internal work.

**MVP — the in-report strip is built and shipped (2026-08-03):**
- [x] Verify data source (fields, granularity, license)
- [x] `tools/market_trends.py` — batch download → filter to state → local slice, with
      lazy in-process index and graceful degradation when no slice exists
- [x] `scout.py --market-refresh <STATE>` — manual refresh (no worker infra invented;
      that decision stays with Phase 3)
- [x] Inline "market snapshot" strip in each deal report — months of supply + temperature,
      sale-to-list, % above ask, price drops, median days to contract, and the deal's
      $/sqft vs the city median (the part a generic dashboard can't show)
- [x] Redfin source attribution in the strip
- [x] Minimum-sample threshold (10 sales) + `NA` handling before any percentage renders
- [x] 64 tests, no network (gzip fixture for refresh, tmp_path slices for lookup)

**Not built — pending the CEO call on whether the standalone dashboard exists at all
(see the competitive reality check above):**
- [ ] `/market?area=…` route in `app.py`
- [ ] Standalone dashboard page for Seattle/Kirkland/Redmond

**Follow-ups surfaced during the build:**
- [ ] Feed market context into the *ranker*, not just the report — the moat example
      ("an over-ask offer pushes cap rate below your floor") needs it in the narrative,
      which means threading it through DealNarrative rather than looking it up at render time
- [ ] Confirm upstream refresh cadence before automating (see Recency above)
- [ ] Deployment sizing: the 954 MB refresh needs disk/memory that small instances
      may not have — couples to Active P1

**Metric definitions (verbatim, Redfin methodology) and display caveats:**

- **Sale-to-list** — "For homes sold during a given period, the average ratio of each home's
  final sale price to its final list price." 0.99 = sold ~1% under ask; 1.01 = ~1% over.
- **% sold above list** — "The percentage of homes sold where the sale price was above the
  most recent list price."
- **Median DOM** — "For homes that went under contract during a given period, the median
  number of days they were listed for before going under contract."

Three caveats that must shape the UI copy:
1. **Both price metrics use the *final* list price, not the original.** A home listed at $1M,
   cut to $900k, sold at $900k scores 1.000 and reads as "sold at asking" despite going 10%
   under its original ask. These metrics systematically understate seller weakness — always
   display `PRICE_DROPS` alongside as the counterweight.
2. **Different cohorts.** Sale-to-list and % above list cover homes that *closed* in the period;
   median DOM covers homes that went *under contract* in it. Same city, same month, different
   sets of houses — don't narrate them as one group.
3. **DOM stops at contract, not closing.** It's a demand-speed signal, not time-to-keys
   (add ~30–45 days for the close).

**Data-quality requirements (QA review, 2026-07-30):**

- **Small-sample cities produce garbage percentages.** Of 555 WA cities, a long tail sells 1–3
  homes/month. Real May 2026 rows: Alger sold 1 home → "100% above list"; Bucoda sold 1 →
  sale-to-list 0.877; Boulevard Park sold 1 → DOM 359. **Require a minimum sample (~10 sales)
  before rendering any percentage or ratio; below it, show the raw count instead.**
- **`NA` is a real value in this file** — 19 rows had `NA` median DOM in May 2026 alone. Parse
  it as null; never coerce to 0.
- **`REGION_TYPE` is `place`, which includes Seattle neighborhoods** — "Beacon Hill", "Bryant",
  and "Cascade Valley" each appear as their own rows alongside "Seattle". City matching against
  `criteria.allowed_cities` will collide with these; match deliberately.

### NOT building (explicitly out of scope)
- Mobile app (web is sufficient)
- MLS integration (requires real estate license)
- Agent marketplace / lead gen (different business)
- Automated offer submission (regulatory minefield)
- React frontend (Jinja2 + chat UX handles it)

---

## Blocked / Deferred

- **Redfin live/CSV API** — flaky for Seattle (region-ID issues return 0 or wrong-region results). ScraperAPI + Rentcast are the working live paths.
- **GreatSchools API** — limited free tier, no good free alternative. Named `school_district` stays null for real listings (nearby-schools + proficiency still work).
