"""
Main pipeline: fetch → screen → enrich → analyze → flag → rank + narrate.

Python controls the 5 deterministic stages.
Claude is called once at the end to rank and narrate the results.

Architecture:
    fetch_listings()          → list[RawListing]
    screen_all()              → list[RawListing] (filtered)
    enrich_all()              → list[EnrichResult]
    analyze_all()             → list[AnalyzedListing]
    flag_all()                → list[FlaggedListing]
    claude_rank_and_narrate() → Shortlist
"""
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import anthropic
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

from tools.analyze import analyze_all
from tools.enrich import enrich_all
from tools.fetch import fetch_listings_async, resolve_city_urls
from tools.mock_ranker import mock_rank_and_narrate
from tools.models import FlaggedListing, InvestmentConfig, Shortlist
from tools.ollama_ranker import ollama_rank_and_narrate
from tools.report import generate_report
from tools.risks import flag_all
from tools.screen import screen_all

logger = logging.getLogger(__name__)
console = Console()

_OUTPUTS_DIR = Path("outputs")
_CLAUDE_MODEL = "claude-sonnet-4-6"


async def run(market: str, config: InvestmentConfig) -> Shortlist:
    """
    Execute the full pipeline for a given market.

    Raises:
        FileNotFoundError: fixtures file is missing
        ValueError: fixtures file is invalid
        SystemExit(1): Claude API failed (analyzed data is saved first)
    """
    _OUTPUTS_DIR.mkdir(exist_ok=True)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    run_log: dict = {"run_id": run_id, "market": market}

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
        transient=True,
    ) as progress:
        task = progress.add_task("Starting...", total=None)

        # ── Stage 1: Fetch ────────────────────────────────────────────────────
        progress.update(task, description="[1/5] Fetching listings...")

        # Auto-resolve Redfin search URLs when none are configured but we have
        # a city list. This lets users search any market without pasting URLs.
        fetch_config = config.fetch
        if (
            fetch_config.data_source == "scraperapi"
            and not fetch_config.scraperapi_search_urls
            and config.criteria.allowed_cities
        ):
            console.log("[dim]Auto-resolving Redfin search URLs for market...[/dim]")
            resolved = await resolve_city_urls(
                config.criteria.allowed_cities,
                max_price=config.criteria.max_price,
                min_beds=config.criteria.min_beds,
                home_types=config.criteria.preferred_home_types,
            )
            if resolved:
                console.log(f"[dim]Resolved {len(resolved)} Redfin URL(s)[/dim]")
                fetch_config = fetch_config.model_copy(
                    update={"scraperapi_search_urls": resolved}
                )
            else:
                console.print(
                    "[yellow]Could not auto-resolve Redfin URLs for this market. "
                    "Try providing search URLs manually.[/yellow]"
                )

        raw = await fetch_listings_async(market, fetch_config)
        run_log["listings_fetched"] = len(raw)
        console.log(f"[dim]Fetched {len(raw)} listings[/dim]")

        # ── Stage 2: Screen ───────────────────────────────────────────────────
        progress.update(task, description="[2/5] Screening...")
        screened, filtered_out = screen_all(raw, config.criteria)
        run_log["screened_out"] = filtered_out
        run_log["listings_passed_screening"] = len(screened)
        console.log(
            f"[dim]Screening: {len(screened)} passed, {len(filtered_out)} filtered[/dim]"
        )

        if not screened:
            console.print("\n[yellow]No listings matched your criteria.[/yellow]")
            console.print(
                "  Tip: relax [bold]max_price[/bold], [bold]min_beds[/bold], "
                "or [bold]max_dom[/bold] in config.yaml"
            )
            _write_run_log(run_log)
            return Shortlist(market=market, deals=[], run_summary="No listings matched criteria.")

        # ── Stage 3: Enrich ───────────────────────────────────────────────────
        progress.update(task, description=f"[3/5] Enriching {len(screened)} listings...")
        enrich_results = await enrich_all(screened, config.enrich)
        enrichment_errors = [r.error for r in enrich_results if r.error]
        run_log["enrichment_failures"] = enrichment_errors
        if enrichment_errors:
            logger.warning("%d enrichment failure(s)", len(enrichment_errors))
        console.log(f"[dim]Enriched {len(enrich_results)} listings[/dim]")

        # ── Stage 4: Analyze ──────────────────────────────────────────────────
        progress.update(task, description="[4/5] Analyzing financials...")
        analyzed = analyze_all(enrich_results, config.financial_assumptions)
        successful = [a for a in analyzed if a.financials.success]
        console.log(
            f"[dim]Financial analysis: {len(successful)}/{len(analyzed)} succeeded[/dim]"
        )

        # Post-enrich school score filter (optional — requires school enrichment)
        if config.criteria.min_school_score is not None:
            before = len(analyzed)
            def _best_school_score(a) -> float | None:
                schools = a.listing.nearby_schools
                if not schools:
                    return None
                scores = [s.proficiency_score for s in schools if s.proficiency_score is not None]
                return max(scores) if scores else None
            analyzed = [
                a for a in analyzed
                if _best_school_score(a) is None  # unknown = don't discard
                or _best_school_score(a) >= config.criteria.min_school_score
            ]
            dropped = before - len(analyzed)
            if dropped:
                console.log(f"[dim]School filter: {dropped} below score {config.criteria.min_school_score:.0f} removed[/dim]")

        # Post-enrich primary suite filter (optional — requires scraping to have run)
        if config.criteria.require_primary_suite:
            before = len(analyzed)
            analyzed = [
                a for a in analyzed
                if a.has_primary_suite is True
                or a.has_primary_suite is None  # unknown = don't discard
            ]
            dropped = before - len(analyzed)
            if dropped:
                console.log(f"[dim]Primary suite filter: {dropped} without primary bedroom removed[/dim]")

        # Post-analysis cap rate filter (optional — set min_cap_rate in config.yaml)
        if config.criteria.min_cap_rate is not None:
            before = len(analyzed)
            analyzed = [
                a for a in analyzed
                if a.financials.cap_rate is not None
                and a.financials.cap_rate >= config.criteria.min_cap_rate
            ]
            dropped = before - len(analyzed)
            if dropped:
                console.log(f"[dim]Cap rate filter: {dropped} below {config.criteria.min_cap_rate:.1%} removed[/dim]")

        if not analyzed:
            console.print("\n[yellow]No listings met the minimum cap rate.[/yellow]")
            console.print("  Tip: lower [bold]min_cap_rate[/bold] in config.yaml or set it to null")
            _write_run_log(run_log)
            return Shortlist(market=market, deals=[], run_summary="No listings met the minimum cap rate.")

        # ── Stage 5: Flag risks ───────────────────────────────────────────────
        progress.update(task, description="[5/5] Flagging risks...")
        flagged = flag_all(analyzed, config.criteria)
        high_risk = [f for f in flagged if f.risks.overall_risk.value == "HIGH"]
        if high_risk:
            console.log(f"[dim]Risk flags: {len(high_risk)} high-risk properties[/dim]")
        console.log(f"[dim]Ready to rank {len(flagged)} properties[/dim]")

    # ── Save analyzed data BEFORE calling Claude ─────────────────────────────
    # If the Claude API fails, the pipeline work isn't lost.
    analyzed_path = _OUTPUTS_DIR / f"{run_id}_analyzed.json"
    analyzed_path.write_text(
        json.dumps([f.model_dump() for f in flagged], indent=2, default=str)
    )

    # ── Rank + narrate ────────────────────────────────────────────────────────
    ranker = config.output.ranker
    if ranker == "ollama":
        console.print(
            f"[dim]Ranking with Ollama ({config.output.ollama_model})...[/dim]"
        )
        try:
            shortlist = ollama_rank_and_narrate(flagged, config)
        except RuntimeError as e:
            console.print(f"\n[red]Ollama ranking failed: {e}[/red]")
            raise SystemExit(1) from e
    elif ranker == "claude":
        console.print("[dim]Ranking with Claude...[/dim]")
        try:
            shortlist = await _claude_rank_and_narrate(flagged, config)
        except anthropic.APIError as e:
            console.print(f"\n[red]Claude ranking failed: {e}[/red]")
            console.print(f"  Analyzed data saved to: [bold]{analyzed_path}[/bold]")
            console.print("  Re-run with [bold]--from-analyzed[/bold] to retry narration only.")
            raise SystemExit(1) from e
    else:
        shortlist = mock_rank_and_narrate(flagged, config)

    # ── Write output ──────────────────────────────────────────────────────────
    safe_market = market.replace(", ", "_").replace(" ", "_").lower()
    output_path = _OUTPUTS_DIR / f"{run_id}_{safe_market}.json"
    output_path.write_text(shortlist.model_dump_json(indent=2))
    report_path = generate_report(shortlist, output_path.with_suffix(".html"), config.financial_assumptions)
    console.print(f"\n[dim]Shortlist saved to {output_path}[/dim]")
    console.print(f"[dim]HTML report: {report_path}[/dim]")

    run_log["shortlist_size"] = len(shortlist.deals)
    _write_run_log(run_log)

    return shortlist


async def run_from_analyzed(analyzed_path: Path, config: InvestmentConfig) -> Shortlist:
    """
    Skip the pipeline stages and re-run only the ranking step on a previously
    saved *_analyzed.json file. Useful when Ollama/Claude failed after the slow
    enrichment + analysis phase.

    Raises:
        FileNotFoundError: analyzed file is missing
        ValueError:        analyzed file is invalid JSON or wrong shape
        SystemExit(1):     ranker failed
    """
    _OUTPUTS_DIR.mkdir(exist_ok=True)

    if not analyzed_path.exists():
        raise FileNotFoundError(f"Analyzed file not found: {analyzed_path}")

    try:
        raw = json.loads(analyzed_path.read_text())
        if not isinstance(raw, list):
            raise ValueError("Expected a JSON array")
        flagged = [FlaggedListing.model_validate(item) for item in raw]
    except (json.JSONDecodeError, Exception) as e:
        raise ValueError(f"Could not load analyzed file: {e}") from e

    console.print(f"[dim]Loaded {len(flagged)} analyzed listings from {analyzed_path}[/dim]")

    ranker = config.output.ranker
    if ranker == "ollama":
        console.print(f"[dim]Ranking with Ollama ({config.output.ollama_model})...[/dim]")
        try:
            shortlist = ollama_rank_and_narrate(flagged, config)
        except RuntimeError as e:
            console.print(f"\n[red]Ollama ranking failed: {e}[/red]")
            raise SystemExit(1) from e
    elif ranker == "claude":
        console.print("[dim]Ranking with Claude...[/dim]")
        try:
            shortlist = await _claude_rank_and_narrate(flagged, config)
        except anthropic.APIError as e:
            console.print(f"\n[red]Claude ranking failed: {e}[/red]")
            raise SystemExit(1) from e
    else:
        shortlist = mock_rank_and_narrate(flagged, config)

    run_id = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    safe_market = config.output.market.replace(", ", "_").replace(" ", "_").lower()
    output_path = _OUTPUTS_DIR / f"{run_id}_{safe_market}.json"
    output_path.write_text(shortlist.model_dump_json(indent=2))
    report_path = generate_report(shortlist, output_path.with_suffix(".html"), config.financial_assumptions)
    console.print(f"\n[dim]Shortlist saved to {output_path}[/dim]")
    console.print(f"[dim]HTML report: {report_path}[/dim]")

    return shortlist


async def _claude_rank_and_narrate(
    flagged: list[FlaggedListing],
    config: InvestmentConfig,
) -> Shortlist:
    """
    Call Claude once with fully-processed listings to produce a ranked shortlist.

    Uses tool_choice="produce_shortlist" to force structured JSON output —
    no fragile text parsing.
    """
    client = anthropic.AsyncAnthropic()

    listings_data = [
        {
            "address": f.listing.address,
            "price": f.listing.price,
            "beds": f.listing.beds,
            "baths": f.listing.baths,
            "sqft": f.listing.sqft,
            "lot_sqft": f.listing.lot_sqft,
            "home_type": f.listing.home_type,
            "school_district": f.listing.school_district,
            "nearby_schools": [
                {
                    "name": s.name,
                    "level": s.level,
                    "distance_miles": round(s.distance_miles, 2) if s.distance_miles else None,
                    "proficiency_score": s.proficiency_score,
                }
                for s in (f.listing.nearby_schools or [])
            ] or None,
            "hoa_fee": f.listing.hoa_fee,
            "days_on_market": f.listing.days_on_market,
            "walk_score": f.walk_score,
            "estimated_monthly_rent": f.estimated_monthly_rent,
            "cap_rate": f.financials.cap_rate,
            "coc_return": f.financials.coc_return,
            "monthly_cashflow": f.financials.monthly_cashflow,
            "noi_annual": f.financials.noi_annual,
            "total_cash_invested": f.financials.total_cash_invested,
            "financial_data_available": f.financials.success,
            "flood_zone": f.risks.flood_zone,
            "risk_level": f.risks.overall_risk.value,
            "risk_flags": [{"code": rf.code, "description": rf.description} for rf in f.risks.flags],
        }
        for f in flagged
    ]

    c = config.criteria
    a = config.financial_assumptions

    user_prompt = f"""You are an experienced real estate investment analyst reviewing pre-screened properties.

Market: {config.output.market}
Target cap rate: {c.target_cap_rate:.1%}
Down payment assumed: {a.down_payment_pct:.0%}
Loan rate: {a.loan_rate_annual:.1%}

{len(flagged)} properties passed initial screening (max price ${c.max_price:,.0f}, \
min {c.min_beds} beds, max DOM {c.max_dom}).

Properties with financial analysis:
{json.dumps(listings_data, indent=2)}

Select the top {config.output.max_shortlist} investment opportunities. \
For each, write 2-3 sentences explaining why the deal is compelling, \
what makes the numbers work, and what to watch out for. \
Be specific about the financials. Rank by overall investment quality \
(cap rate, cash flow, and risk-adjusted return). \
Properties with financial_data_available=false should rank lower. \
Properties with HIGH risk should be noted prominently in the narrative."""

    # Use a minimal schema for the tool call — the full inlined Shortlist schema is
    # too large and causes Claude to omit the required `deals` array.
    # We only need address + narrative + risk_level; all numeric fields are overwritten
    # from pipeline data immediately after.
    minimal_schema = {
        "type": "object",
        "required": ["deals", "run_summary"],
        "properties": {
            "deals": {
                "type": "array",
                "description": "Ranked list of investment opportunities",
                "items": {
                    "type": "object",
                    "required": ["rank", "address", "price", "risk_level", "narrative"],
                    "properties": {
                        "rank":      {"type": "integer"},
                        "address":   {"type": "string"},
                        "price":     {"type": "number"},
                        "risk_level":{"type": "string", "enum": ["LOW", "MEDIUM", "HIGH"]},
                        "narrative": {"type": "string", "description": "2-3 sentence investment thesis"},
                    },
                },
            },
            "run_summary": {
                "type": "string",
                "description": "One sentence summarising how many properties were reviewed and selected",
            },
        },
    }

    response = await client.messages.create(
        model=_CLAUDE_MODEL,
        max_tokens=8192,
        tools=[
            {
                "name": "produce_shortlist",
                "description": "Output the final ranked shortlist of investment opportunities",
                "input_schema": minimal_schema,
            }
        ],
        tool_choice={"type": "tool", "name": "produce_shortlist"},
        messages=[{"role": "user", "content": user_prompt}],
    )

    raw = response.content[0].input
    # Build a valid Shortlist from the minimal response; numeric fields are
    # overwritten from pipeline data in the loop below.
    shortlist_data = {
        "market": config.output.market,
        "run_summary": raw.get("run_summary", ""),
        "deals": [
            {
                "rank":       d.get("rank", i + 1),
                "address":    d.get("address", ""),
                "price":      d.get("price", 0),
                "risk_level": d.get("risk_level", "MEDIUM"),
                "narrative":  d.get("narrative", ""),
            }
            for i, d in enumerate(raw.get("deals") or [])
        ],
    }
    shortlist = Shortlist.model_validate(shortlist_data)

    # Overwrite all pipeline-derived fields from authoritative pipeline data.
    # This prevents Claude from returning financials in wrong units (e.g. pct vs decimal).
    flagged_by_address = {f.listing.address.strip().lower(): f for f in flagged}
    for deal in shortlist.deals:
        f = flagged_by_address.get(deal.address.strip().lower())
        if f is None:
            continue
        deal.price = f.listing.price
        deal.beds = f.listing.beds
        deal.baths = f.listing.baths
        deal.days_on_market = f.listing.days_on_market
        deal.cap_rate = f.financials.cap_rate
        deal.coc_return = f.financials.coc_return
        deal.monthly_cashflow = f.financials.monthly_cashflow
        deal.noi_annual = f.financials.noi_annual
        deal.monthly_mortgage = f.financials.monthly_mortgage
        deal.estimated_monthly_rent = f.estimated_monthly_rent
        deal.walk_score = f.walk_score
        deal.flood_zone = f.risks.flood_zone
        deal.risk_level = f.risks.overall_risk.value
        deal.tax_assessed_land = f.listing.tax_assessed_land
        deal.tax_assessed_improvement = f.listing.tax_assessed_improvement
        deal.appreciation = f.appreciation
        deal.latitude = f.listing.latitude
        deal.longitude = f.listing.longitude
        deal.listing_url = f.listing.listing_url
        deal.year_built = f.listing.year_built
        deal.nearby_schools = f.listing.nearby_schools
        deal.solar_ghi_annual = f.listing.solar_ghi_annual
        deal.bike_score = f.bike_score
        deal.transit_score = f.transit_score
        deal.has_primary_suite = f.has_primary_suite
        deal.has_garage = f.has_garage
        deal.garage_spaces = f.garage_spaces
        deal.has_basement = f.has_basement
        deal.basement_finished = f.basement_finished
        deal.has_fireplace = f.has_fireplace

    return shortlist


def _resolve_schema_refs(schema: dict) -> dict:
    """
    Inline all $ref references in a JSON schema so Claude receives a self-contained
    schema with no unresolvable $ref pointers.
    """
    defs = schema.get("$defs", {})

    def _inline(obj):
        if isinstance(obj, dict):
            if "$ref" in obj:
                ref_name = obj["$ref"].split("/")[-1]
                return _inline(defs.get(ref_name, obj))
            return {k: _inline(v) for k, v in obj.items() if k != "$defs"}
        if isinstance(obj, list):
            return [_inline(item) for item in obj]
        return obj

    return _inline(schema)


def _write_run_log(entry: dict) -> None:
    log_path = _OUTPUTS_DIR / "run_log.jsonl"
    with log_path.open("a") as f:
        f.write(json.dumps(entry, default=str) + "\n")
