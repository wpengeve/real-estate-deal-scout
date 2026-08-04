# real-estate-deal-scout

A Python project for scouting real estate deals.

## Setup

```bash
source .venv/bin/activate
pip install -e ".[dev]"     # dependencies live in pyproject.toml — there is no requirements.txt
```

## gstack

### Recommended skills for this project

| Stage | Skill |
|-------|-------|
| Brainstorming features | `/office-hours` |
| Reviewing a plan (strategy) | `/plan-ceo-review` |
| Reviewing a plan (architecture) | `/plan-eng-review` |
| Debugging errors | `/investigate` |
| Testing the app | `/qa` |
| Code review before merge | `/review` |
| Ready to ship / create PR | `/ship` |
| Post-ship doc updates | `/document-release` |
| Working with prod/live data | `/careful` |
| Adversarial code review | `/codex` |

### Notes

- Python 3.12, virtual env at `.venv/`
- Run `gstack-upgrade` to get the latest skill versions

## TPM Policy — autonomous integration decisions

These rules apply to all third-party API and data-source decisions. Follow them
without asking the user unless a decision falls outside these categories.

### Data sourcing — preference order
1. **Already in a page we fetch** — if the data is embedded in HTML we already
   retrieve (e.g. Redfin listing page), parse it there. No extra API call.
2. **Free public API / no key required** — e.g. NREL solar, FEMA flood, census.
3. **Free tier with API key** — e.g. HUD FMR, Walk Score free tier. Add env var,
   degrade gracefully when key is absent.
4. **Paid API** — requires explicit user approval before adding.

### Graceful degradation — non-negotiable
- A missing API key must never crash the pipeline. Return `None`, log a warning.
- Every external call gets a timeout. No unbounded waits.
- Partial enrichment (some fields `None`) is always acceptable.

### When evaluating a new data need
1. Check if Redfin listing page already has it (scrape first).
2. Check if an existing API response already returns it (avoid duplicate calls).
3. Prefer a single well-targeted request over polling multiple sources.
4. If the best option is paid: note it in a comment and leave a `TODO` — don't
   integrate until the user approves.

### API keys — naming convention
`WALKSCORE_API_KEY`, `HUD_API_KEY`, `ANTHROPIC_API_KEY`, `NREL_API_KEY` — always
read from env, never hardcoded. Document new keys in `README.md`.

### When NOT to decide autonomously
- Adding a paid dependency (cost > $0)
- Changing the primary data source for listings (currently Redfin CSV)
- Any change that affects user-facing output format significantly
