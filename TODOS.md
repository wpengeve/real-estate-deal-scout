# TODOS

Last updated: 2026-08-12

---

## Status at a glance

*Read this bit to catch up; everything below is the detail.*

**Working today, end to end, on a laptop.** Describe what you want in plain English (or edit
`config.yaml`) → the pipeline pulls live listings, screens them, enriches with rent/schools/
solar/zoning data, runs the financial analysis against *your* down payment and rate, flags
risks, and produces an AI-ranked HTML report with maps, live sliders, and area market context.
434 tests passing.

**Not deployed.** It runs on your machine only — there's no URL to send anyone. That's the
one thing keeping Phase 1 open, and it's a decision (which host, where reports and accounts
live), not a bug. Nothing is broken; see **Active → P1**.

**Most recent work (Aug 2026): Market Intelligence.** Every deal now shows how its city's
market is behaving — months of supply, sale-to-list, share sold over asking, days to
contract — plus how that home's $/sqft compares to the city median. The AI ranker sees the
same numbers, so narratives can weigh price against the local market. Free Redfin data, no
API key. Run `python scout.py --market-refresh WA` once to switch it on.

**Decided, so we don't revisit it:** no standalone market dashboard (Redfin already publishes
one free from the same data), and no worker infrastructure yet (refresh is a manual command).

**Next up when you want it:** P1 deployment. Smaller open items are P2 (tighter listing
scope) and P3 (school district names).

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
- **434 tests passing**
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

**Implication — accepted 2026-08-03.** The in-report market snapshot strip is the real
product and was built first; the standalone dashboard is not being built. See the checklist
below.

**Data source — VERIFIED 2026-07-30. Use Redfin Data Center city market tracker.**

Source: `https://redfin-public-data.s3.us-west-2.amazonaws.com/redfin_market_tracker/city_market_tracker.tsv000.gz`
(954 MB gzipped, TSV, no API key, no quota, no rate limit)

Every metric the pillar needs is present, with MoM and YoY deltas for each:
`AVG_SALE_TO_LIST`, `SOLD_ABOVE_LIST`, `MEDIAN_DOM`, `INVENTORY`, `MONTHS_OF_SUPPLY`,
`HOMES_SOLD`, `NEW_LISTINGS`, `PENDING_SALES`, `PRICE_DROPS`, `OFF_MARKET_IN_TWO_WEEKS`,
`MEDIAN_SALE_PRICE`, `MEDIAN_LIST_PRICE`, `MEDIAN_PPSF`.

Confirmed against live data:
- **Coverage** — 558 WA cities in the shipped 3-year slice (555 if narrowed to
  2024+, the window this check originally used). Seattle, Bellevue, Kirkland,
  Redmond all populated.
  (Seattle May 2026: sale-to-list 1.008, 30.9% above list, 11 median DOM, 799 sold, 2111 inventory.)
- **Granularity** — city × month × property type. Property types include
  `Multi-Family (2-4 Unit)`, so rental-investor cuts are possible, not just `All Residential`.
- **Clean shape** — one row per (city, period, property type). No seasonally-adjusted
  duplicates in this file (`IS_SEASONALLY_ADJUSTED` is always false), all periods 30-day.
- **Recency — RESOLVED 2026-08-03. Publication is irregular; poll, don't schedule.**
  File stamped 2026-06-02, latest period 2026-05-31, and unchanged two months later.
  Checked all five monthly trackers (city, county, state, ZIP, national): **every one is
  stamped within four minutes of the others on 2026-06-02 and none has moved since.** So the
  whole monthly batch publishes at once and simply hasn't run — the city file did not move to
  a different path. A blind monthly cron would therefore re-download ~950 MB to produce a
  byte-identical slice. **Design consequence:** `refresh()` HEADs the URL first and skips the
  download when `Last-Modified` matches what the local slice was built from (`--force`
  overrides). The skip path costs ~0.5s. The UI carries an honest "data as of <period>" line.
  Fine for "hot or cooling"; never promise current-month data.

**Design constraint — rows are unsorted by region.** Extracting one city means scanning the
whole 954 MB file, so this must be a **cached monthly batch refresh, never a per-request fetch**.
Filtering WA to 2024+ yields 30,781 rows / 15 MB. Superseded by what actually
shipped: 3 years of history and 23 of the 58 columns, landing at 43,341 rows /
7.1 MiB. Either way, small enough to persist locally
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
- [x] 102 tests, no network (gzip fixture for refresh, tmp_path slices for lookup)

**Ranker integration — done (2026-08-03):**
- [x] Market context threaded through the pipeline (`FlaggedListing.market_context` →
      `DealNarrative.market_context`), attached at all four entry points (`run`,
      `run_from_analyzed`, `run_single_property`, `run_multi_property`) from the
      local slice — no network
- [x] Claude ranking prompt carries an `area_market` object per listing plus guidance
      encoding the caveats (final-list-price, staleness, "work it into Line 2 or 3, never
      add a fourth line"), so narratives can reason about pricing vs the city and expected
      competition
- [x] Percentages withheld from the *model* below the sample threshold, not just from the
      reader — the ranker never sees "100% above list" off one sale
- [x] Report prefers pipeline-attached context, falling back to a lookup for shortlists
      saved before this existed, so strip and narrative can't disagree

**Independent review — 2026-08-09.** Two subagents audited the pillar: one adversarial code
review of the whole diff, one verifying every number written into the docs. Six real defects
found and fixed, all in code the suite already covered and still passed:

| Defect | Effect | Fix |
|---|---|---|
| `$/sqft vs city median` bypassed the sample gate | Report printed "100% above the Alger median" from **one sale**, directly above the note saying such figures are hidden. Ranker prompt got the same number with no `rates_withheld` caveat | Gated on `has_enough_sales` in both `report.py` and `pipeline.py` |
| Refresh skip trusted `exists()` + stamp | A truncated or zero-row slice matched forever: no market data in any report, CLI printing "✓ Already current". Only `--force` recovered | Skip now also requires `rows > 0`, nonzero file size, and adequate `cutoff`/`source_url` — see the second review pass below, which found the first attempt at this didn't reach production |
| `history_years` / `source_url` ignored by skip | Asking for a wider window silently returned the narrow slice | Both recorded in `.meta.json` and compared |
| Index cache keyed on path only, invalidated in-process | The FastAPI app never calls `refresh()` (that's a separate `scout.py` process), so it served its first-seen slice until restart | Cache fingerprinted on `(mtime_ns, size)` |
| Single-entry cache | A two-state shortlist re-parsed the 43k-row slice on **every** lookup, blocking inside the event loop | Cache is a dict keyed by path |
| City/period strings interpolated raw into HTML | `Town & Country` — a real city in the shipped slice — emitted invalid markup | `html.escape()` on all third-party strings |

Nine regression tests added, each verified to fail against the pre-fix code. One existing test
(`test_small_sample_suppresses_percentages`) was passing by coincidence — its fixture's
`median_ppsf` happened to equal the deal's $/sqft, so the leak rendered "in line with" instead
of a percentage. Suite: **402 passing**.

Docs audit: every substantive data claim held (Seattle May 2026 figures exact to the digit, the
10-sale threshold, the 19 `NA` DOM rows, the 58-column upstream schema, the 954 MiB source, the
five-tracker timestamp cluster, `MARKET_TRENDS_DIR`/`--market-refresh`/`--force`, gitignore
status). Five stale *numbers* were wrong and are now corrected: test counts in three places,
a row count off by one, and "555 cities" which was only true under an unstated 2024+ filter.

Left open deliberately (low severity, none reproduced) — **three of four closed 2026-08-12,
see "Follow-up sweep" below:**
- [x] Concurrent refreshes of the same state share one `.partial` path with no lock
- [x] `.gitignore` covers `data/market_trends_*.tsv` but not `*.tsv.partial`, so a killed
      refresh leaves an untracked file
- [ ] `_clean(PERIOD_BEGIN) < cutoff` is a string compare — a reformatted upstream date would
      drop every row (now self-healing, since a zero-row slice re-downloads)
- [x] Addresses with a country suffix (`…, WA 98118, USA`) parse to `None`, so market context
      silently never appears; only a run with *zero* matches logs anything

**Second review pass — 2026-08-11, before merging the fixes.** Two more subagents verified the
fix commit itself rather than the feature. One found a blocker that invalidated the headline fix:

- **The hardened skip check was unreachable.** `scout.py` kept its own weaker copy of the rule
  (`local_last_modified == upstream_last_modified`) and `return`ed *before* `refresh()` was
  called. `scout.py` is the only production caller, so a truncated slice still printed
  "✓ Already current" with 0 downloads — behaviour byte-identical to before the fix. Every new
  test called `refresh()` directly, one layer below the bug, so the suite stayed green.
  Fixed by extracting `market_trends.is_current()` as the single definition of "current" and
  having both the CLI and `refresh()` ask it. The weaker `local_last_modified()` helper is
  deleted rather than left around to be reached for again.
- **Migration cost.** The `cutoff` key didn't exist in sidecars written by the old code, so
  every existing install would have re-downloaded 950MB once the blocker above was fixed. A
  missing `cutoff` is now grandfathered.
- **Annual churn.** `cutoff` derives from `date.today().year`, so equality comparison would
  re-download every January to produce a slice with *less* history than it replaced. Now
  compares coverage (`stored <= required`), not equality.

Verified live afterwards: the real CLI against the real slice and its pre-`cutoff` sidecar still
skips in ~1s with no download. Five more tests added, this time driving `scout.main()`; the three
covering new behaviour were each confirmed to fail against the pre-fix code. Suite: **407**.

Follow-ups this pass raised, not blocking:
- [x] **Escaping made consistent across `report.py` (2026-08-11).** Added `_esc()` and routed
      every untrusted interpolation through it: address, zoning, home type, flood zone, school
      names, verdict-reason bullets, the LLM narrative, and the photo/listing URLs (which sit in
      attributes, so an embedded quote could start a new one). Writing the tests turned up a
      site the review missed — flood zone also reaches the risk-flag bullets — so those are now
      escaped at the single render point rather than at the ~10 places that build a reason.
      Separately, the Leaflet map payload was `json.dumps` output sitting raw inside `<script>`:
      `json.dumps` escapes quotes but not `</script>`, so an address or narrative containing it
      would close the block early and spill the rest into the document as markup. `<` is now
      unicode-escaped in that payload. 13 tests added, 12 of which fail against the pre-fix code
      (the 13th is the guard that clean data is not mangled).
- [x] **Cache ceiling rose.** ~76MB resident per cached state index (measured, WA). Pre-fix the
      cache held one; now one per state slice on disk, no eviction. Single-state installs are
      unaffected — a multi-state deployment pays 76MB × N. A 2-entry LRU would keep the win.
      **Done 2026-08-12** — see below.
- [ ] `(mtime_ns, size)` is weaker than it sounds: `rsync -a`/`tar -xp`/`cp -p` restore mtimes,
      and a same-size rewrite then goes unseen. Narrow, and all such flows restart the process.
- [ ] `test_zero_row_slice_forces_download` asserts as *desirable* that a state with genuinely
      no upstream rows re-downloads 950MB every invocation, with no backoff.

**Follow-up sweep — 2026-08-12.** Cleared four of the seven items left open by the two review
passes. No new review; these were the known-and-parked ones.

| Item | Fix |
|---|---|
| Country-suffixed addresses parsed to `None` | `parse_city_state()` strips a trailing US country segment before reading the state. `US` had to be handled there rather than left to the two-letter state check, which would otherwise have accepted it and read `WA 98118` as the city. Non-US countries still fail the parse — the slice is US-only, so trimming them would risk matching a same-named US city |
| Unbounded index cache | 2-entry LRU (`_INDEX_CACHE_MAX`), all writes routed through `_cache_put()`, cache hits re-marked as recent. Keeps the two-state win that motivated the dict; ceiling no longer scales with states touched |
| Shared `.partial` scratch path | Now unique per process and thread, so a failed refresh can't unlink a concurrent one's in-flight file. The final `replace` was already atomic |
| `.gitignore` missed `*.tsv.partial` | Added `data/market_trends_*.partial` (the existing `*.tsv` glob stops at `.tsv`) |

14 tests added; 10 were confirmed to fail against the pre-fix code, and the other 4 are guards
that the changes don't break the clean path (no scratch file after a successful refresh,
eviction can't change a lookup result, `…, Canada` and a country-only tail still fail to parse).
Verified live afterwards against the real 43,341-row WA slice: the suffixed and bare forms of a
Seattle address both resolve to the same row (799 homes sold, May 2026), and the refresh CLI
still skips in ~1.4s with no download. Suite: **434 passing**.

The three still open are genuine judgment calls, not oversights: the string-compare cutoff is
self-healing, the fingerprint weakness needs a content hash on a 7MB file every lookup to fix,
and the zero-row re-download is a real trade against trusting a possibly-truncated slice.

**Standalone dashboard — decided 2026-08-03: NOT building.** Redfin already publishes an
equivalent free public dashboard from the same data (see the competitive reality check
above), so it would be the least defensible part of the pillar. The data layer is done, so
this is roughly a day's work if that call is ever reversed.
- [ ] ~~`/market?area=…` route in `app.py`~~
- [ ] ~~Standalone dashboard page for Seattle/Kirkland/Redmond~~

**Deployment sizing — MEASURED 2026-08-03. Not a constraint; the earlier concern was wrong.**
The worry was that a ~950 MB download needing "several GB" would not fit a small instance.
Measured on a real full refresh (`/usr/bin/time -l`):

| | Measured |
|---|---|
| Peak resident memory | **61 MB** |
| Wall time | 55 s (31 s CPU) |
| Slice written to disk | **7.1 MB** |
| 950 MB file stored on disk | never — streamed and discarded |

Because `refresh()` streams the gzip and writes only the filtered rows, the big file never
lands on disk and never accumulates in memory. It runs on any tier, including free 256 MB
instances. **No need to refresh locally and ship the slice**, though that remains an option
if a host meters bandwidth (~950 MB per *published* update, and the conditional check makes
every other run ~0.5 s and near-zero bytes).

The one real deployment note is ephemeral disk: a 7.1 MB slice vanishes on redeploy. Either
re-run the command after deploying, or set **`MARKET_TRENDS_DIR`** to a persistent volume
(added 2026-08-03; defaults to `data/`).
- [x] Measure actual refresh footprint
- [x] `MARKET_TRENDS_DIR` env override so the slice can live on a mounted volume

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
  Specifics, confirmed 2026-03-23 and worth keeping because the shipped config still
  carries one of these values: `region_id=16163` (Seattle city) returned **0 listings**,
  and `region_id=16904` returned **350 San Diego listings**. Root cause never found —
  likely server-side blocking or a changed internal region-ID mapping.
  `config.yaml.example` ships `redfin_region_id: 16904`, so anyone flipping
  `data_source: redfin` for a Seattle search gets San Diego homes with no error. The
  endpoint is undocumented and can break without notice. Also note HUD rent estimates
  are county-level averages, not property-specific.
- **GreatSchools API** — limited free tier, no good free alternative. Named `school_district` stays null for real listings (nearby-schools + proficiency still work).
