# TODOS

Last updated: 2026-03-25

---

## Done ✓

- **Real data source** — Redfin CSV integration (`data_source: csv` in config.yaml)
- **Rent comps** — HUD Fair Market Rents API (free, ZIP→county crosswalk via USPS)
- **Zoning fields** — `zoning` field + full `ZoningPotential` model (ADU/DADU/HB1110)
- **Claude ranking** — `ranker: claude` mode active via `claude-opus-4-6`
- **Async enrichment** — `enrich_all()` already async with `asyncio.gather()`
- **Appreciation signals** — `AppreciationSignals` model (price-to-rent, land value %, renovation candidate)
- **School data** — `SchoolInfo` model with NCES data + proficiency scores

---

## Active

### P1 — Test live Redfin CSV + HUD FMR end-to-end

**What:** Set `data_source: csv` and `csv_path: data/redfin.csv` in config.yaml and run a full pipeline on real Seattle listings.

**Why:** All integrations exist but haven't been validated together on real data.

**Blocker:** Redfin live API (`data_source: redfin`) confirmed broken for Seattle — region_id 16163 returns 0 listings, and a wrong ID (16904) returned San Diego listings. Use CSV export workaround.

**Effort:** XS — just run it and fix any field mapping issues.

---

### P1 — USPS ZIP Crosswalk reliability

**What:** Validate that auto-detection of county from listing ZIP works for all listings in the CSV, and handle failures gracefully.

**Why:** HUD FMR rent lookups depend on county FIPS. If crosswalk fails for a ZIP, rent estimate is skipped and financial analysis can't run.

**Effort:** S

---

### P2 — Walk Score API key

**What:** Get a free API key at walkscore.com and add `WALKSCORE_API_KEY` to `.env`.

**Why:** Without it, `walk_score` is null for all real listings, and the `walkscore_min` screen filter is skipped.

**Effort:** XS — 5-minute signup.

---

### P2 — Anthropic API key

**What:** Add `ANTHROPIC_API_KEY` to `.env` and set `ranker: claude` in config.yaml.

**Why:** Currently running Ollama (local) ranker. Real Claude narration produces better investment theses.

**Effort:** XS — key exists, just needs to be wired in.

---

### P3 — School district data for real listings

**What:** Populate `school_district` field from real data. Current sources return it blank.

**Why:** School district is a strong signal for family-home rentals and resale value.

**Context:** GreatSchools API has a limited free tier. Geocoding to district boundary is an alternative. No good free option identified yet.

**Effort:** M