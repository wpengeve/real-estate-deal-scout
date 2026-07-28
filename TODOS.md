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
- **298 tests passing**
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

**Key catch — needs a new data source.** The current pipeline fetches *active* listings;
these metrics need *sold/closed* transaction data (list price + final sale price).
Candidates (prefer free per CLAUDE.md):
- **Redfin Data Center** — free downloadable monthly area metrics (sale-to-list, % sold
  above list, median DOM, inventory) by metro/city/ZIP. Closest match; verify fields/license.
- **Rentcast market statistics** — key already present; confirm whether it exposes sale-to-list.

**Suggested MVP (when prioritized):**
- [ ] Verify data source (fields, granularity, license) — do this first, cheap de-risk
- [ ] `tools/market_trends.py` — fetch + cache area dataset (reuse existing cache pattern)
- [ ] `/market?area=…` route in `app.py`
- [ ] Dashboard page: sale-to-list %, % over/under asking, median DOM, inventory (Seattle/Kirkland/Redmond)
- [ ] Later: inline "market snapshot" strip in each deal report for area context

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
