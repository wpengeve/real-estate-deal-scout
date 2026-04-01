# TODOS

Last updated: 2026-04-01

---

## Done ✓

- **Real data source** — Redfin CSV integration, 9 CSVs loaded, deduplicated by address
- **HUD Fair Market Rents** — ZIP→county crosswalk, 1.25× multiplier for Seattle market
- **KC Assessor enrichment** — zoning code, tax assessed value (land + improvement)
- **Zoning potential** — ADU/DADU eligibility, HB 1110 duplex rights, development score 1–5
- **Appreciation signals** — price-to-rent (GRM), assessment ratio, land value %, renovation flag
- **School data** — NCES EDGE MapServer (2425 schema) + Urban Institute proficiency scores
- **Solar enrichment** — NREL Solar Resource API, GHI kWh/m²/day → peak sun hours, cached
- **Ollama ranker** — local llama3.1:8b ranking (mock + ollama modes)
- **HTML report** — Leaflet maps, live financial sliders, Show top N filter, schools, solar
- **City filter** — `allowed_cities` in screening removes far-out listings (Cle Elum, Puyallup)
- **Dashboard polish** — Walk Score link, CoC color on load, sun hrs/day display, DOM grammar

---

## Active

### P1 — Anthropic API key

**What:** Add real `ANTHROPIC_API_KEY` to `.env` and set `ranker: claude` in config.yaml.

**Why:** Ollama (llama3.1:8b) ranks poorly — a 6.35% cap rate deal ranked #9 instead of #1.
Claude produces accurate, data-grounded narratives and ranks by actual investment merit.

**Effort:** XS — just add the key.

---

### P2 — Walk Score API key

**What:** Waiting on approval from walkscore.com (application submitted).

**Why:** `walk_score` is null for all listings — shows "Look up →" link as fallback.
Once key arrives, re-enable `walkscore_min` in config (currently set to 0).

**Effort:** XS — key arrives, add to `.env`, bump `walkscore_min` back to 50.

---

### P2 — Validate rent estimates

**What:** Spot-check 5–10 listings against Zillow Rent Zestimate or Rentometer to see
how accurate the HUD × 1.25 multiplier is for Seattle.

**Why:** All cashflows are negative right now — if HUD rents are 20–30% below market,
tuning the multiplier to 1.4–1.5 would give a more realistic picture.

**Effort:** S — manual check + update `hud_rent_multiplier` in config.

---

### P3 — Tighter Redfin CSVs

**What:** Current 9 CSVs were downloaded with a wide geographic scope and included
Cle Elum (~80mi) and Puyallup (~35mi). City filter now removes them at screening,
but fresh downloads scoped to Seattle + close Eastside would improve data quality.

**Why:** Fewer junk listings = faster pipeline, cleaner results.

**Effort:** S — download ~7 city-scoped CSVs, update csv_paths in config.

---

## Blocked / Deferred

- **Redfin live API** — broken for Seattle (returns 0 or wrong region). CSV export is the workaround.
- **GreatSchools API** — limited free tier, no good free alternative for school district data. `school_district` field currently null for real listings.