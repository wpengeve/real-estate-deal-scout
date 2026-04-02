# TODOS

Last updated: 2026-04-02

---

## Done ✓

- **Real data source** — Redfin CSV integration, 9 CSVs loaded, deduplicated by address
- **HUD Fair Market Rents** — ZIP→county crosswalk, 1.0× multiplier (validated against Zillow Apr 2026)
- **KC Assessor enrichment** — zoning code, tax assessed value (land + improvement)
- **Zoning potential** — ADU/DADU eligibility, HB 1110 duplex rights, development score 1–5
- **Appreciation signals** — price-to-rent (GRM), assessment ratio, land value %, renovation flag
- **School data** — NCES EDGE MapServer (2425 schema) + Urban Institute proficiency scores
- **Solar enrichment** — NREL Solar Resource API, GHI kWh/m²/day → peak sun hours, cached
- **Ollama ranker** — local llama3.1:8b ranking (mock + ollama modes)
- **HTML report** — Leaflet maps, live financial sliders, Show top N filter, schools, solar
- **City filter** — `allowed_cities` in screening removes far-out listings (Cle Elum, Puyallup)
- **Dashboard polish** — Walk Score link, CoC color on load, sun hrs/day display, DOM grammar
- **Chat intake** — `--chat` CLI flag: Claude tool-use extracts InvestmentConfig from natural language

---

## Active

### P1 — Anthropic API key

**What:** Add real `ANTHROPIC_API_KEY` to `.env` and set `ranker: claude` in config.yaml.

**Why:** Ollama (llama3.1:8b) ranks poorly — a 6.35% cap rate deal ranked #9 instead of #1.
Claude produces accurate, data-grounded narratives and ranks by actual investment merit.
Also needed for `--chat` mode (chat intake uses Claude for config extraction).

**Effort:** XS — just add the key.

---

### P2 — Walk Score API key

**What:** Waiting on approval from walkscore.com (application submitted).

**Why:** `walk_score` is null for all listings — shows "Look up →" link as fallback.
Once key arrives, re-enable `walkscore_min` in config (currently set to 0).

**Effort:** XS — key arrives, add to `.env`, bump `walkscore_min` back to 50.

---

### P3 — Tighter Redfin CSVs

**What:** Current 9 CSVs were downloaded with a wide geographic scope and included
Cle Elum (~80mi) and Puyallup (~35mi). City filter now removes them at screening,
but fresh downloads scoped to Seattle + close Eastside would improve data quality.

**Why:** Fewer junk listings = faster pipeline, cleaner results.

**Effort:** S — download ~7 city-scoped CSVs, update csv_paths in config.

---

## Platform Roadmap (CEO Review 2026-04-02)

**Vision:** Scale from personal CLI tool → multi-user real estate investment agent.
Any user describes their financial situation and goals; the platform finds and
explains the best-matching investment deals for *their* specific profile.

**Core moat:** Personalized financial analysis — every cap rate, CoC, and cashflow
is computed for *your* down payment and rate, then explained by AI.

### Phase 1 — "Anyone can use it" (current focus)

**Goal:** Share a URL. Someone who isn't you uses it.

- [ ] **Live listings API** — evaluate Rentcast / ATTOM / RapidAPI for multi-market
      live data. This is the single biggest unknown. Nothing else in Phase 2 works
      without it. Target: pick an API this week.
- [ ] **FastAPI wrapper** — POST /run endpoint; returns report URL (S3 or Render blob)
- [ ] ~~Chat intake~~ ✓ done — `--chat` flag with Claude tool-use extraction

### Phase 2 — "Multi-market + accounts"

- [ ] **PostgreSQL schema** — users, saved_searches, report_runs
- [ ] **Auth** — email + magic link (avoid OAuth complexity for MVP)
- [ ] **Multi-market enrichment** — handle non-KC markets gracefully
      (HUD rent and schools already work nationally; KC Assessor is Seattle-only)
- [ ] **Shareable report URLs** — 30-day TTL, stored in DB

### Phase 3 — "Retention + alerts"

- [ ] **Background workers** — re-run saved searches daily (RQ or Celery)
- [ ] **Email alerts** — new listings match saved criteria → digest (SendGrid/Resend)
- [ ] **Feedback loop** — thumbs up/down on rankings → stored for future ranking context

### NOT building (explicitly out of scope)

- Mobile app (web is sufficient)
- MLS integration (requires real estate license)
- Agent marketplace / lead gen (different business)
- Automated offer submission (regulatory minefield)
- React frontend (Jinja2 + HTMX handles the chat UX)

---

## Blocked / Deferred

- **Redfin live API** — broken for Seattle (returns 0 or wrong region). CSV export is the workaround until a live API is chosen.
- **GreatSchools API** — limited free tier, no good free alternative. `school_district` field currently null for real listings.