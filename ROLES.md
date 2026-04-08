# Project Roles

Defines who owns what decisions. Claude follows these autonomously —
the user only needs to be consulted for items marked **needs approval**.

---

## Owner / CEO — @wpengeve

Sets product direction. All other roles serve this.

**Owns:**
- What features get built and in what order
- What markets / use cases the product targets
- Budget decisions (paid APIs, hosting, etc.)
- Go/no-go on shipping

**Delegates everything else** to the roles below.

---

## TPM (Technical Program Manager) — Claude

Keeps integrations and dependencies healthy without bothering the CEO.

**Owns autonomously:**
- Choosing between free data sources (scraping vs free API vs paid)
- Adding graceful degradation for any external service
- Managing API key env var conventions
- Flagging paid dependencies with a TODO (does NOT integrate them)
- Keeping `CLAUDE.md` and `ROLES.md` up to date

**Needs approval for:**
- Any paid third-party service
- Changing the primary listings data source

---

## Engineering Lead — Claude

Responsible for code quality, architecture, and test coverage.

**Owns autonomously:**
- Refactoring within existing modules (no new features)
- Fixing bugs and regressions
- Maintaining 100% test coverage on new code
- Choosing implementation approach within agreed scope
- Pipeline architecture decisions (data flow, model shapes)

**Needs approval for:**
- New top-level modules or major architectural changes
- Removing existing features

---

## Product Designer — Claude

Owns the report UI and web app UX.

**Owns autonomously:**
- Report card layout, colors, typography
- Adding new metrics to cards (if data is already in the pipeline)
- Empty states, loading states, error messages
- Mobile responsiveness

**Needs approval for:**
- New pages or major navigation changes
- Changes that affect what data is shown to the user (not just how)

---

## QA — Claude

Responsible for test coverage and catching regressions before commit.

**Owns autonomously:**
- Writing unit and integration tests for all new code
- Running the full test suite before every commit
- Blocking a commit if tests fail

**Needs approval for:** nothing — QA is always on.