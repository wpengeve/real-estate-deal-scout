"""
Real Estate Deal Scout — CLI entry point.

Usage:
    scout [--market "Seattle, WA"] [--config config.yaml] [--max-shortlist 5]
    scout --from-analyzed outputs/20260320-120000_analyzed.json
"""
import argparse
import asyncio
import logging
import re
import sys
from pathlib import Path

import yaml
from dotenv import load_dotenv

# load_dotenv() must run before local imports so that module-level os.getenv()
# calls in enrich.py, crosswalk.py etc. pick up keys from .env
load_dotenv()

from pydantic import ValidationError
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box

from pipeline import run, run_from_analyzed
from tools import market_trends
from tools.chat_intake import run_chat_intake
from tools.models import InvestmentConfig, Shortlist

console = Console()
logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")

# Seattle/KC zoning prefix → human-readable description.
# KC codes are granular (NR1/NR2/NR3) and may carry MHA suffixes like "(M)" or "(M1)".
# _format_zoning() strips the suffix and matches by prefix.
_ZONING_DESCRIPTIONS = {
    "NR":  "Neighborhood Residential",   # replaced SF zoning; 3–6 units/lot
    "LR":  "Lowrise Multifamily",        # townhouses, small apartments (LR1/LR2/LR3)
    "MR":  "Midrise",                    # mid-density apartments
    "HR":  "Highrise",                   # high-density towers
    "NC":  "Neighborhood Commercial",    # mixed-use, ground-floor retail
    "C1":  "Neighborhood Commercial",
    "C2":  "General Commercial",
    "IC":  "Industrial/Commercial",
    "IG":  "Industrial General",
}


def _format_zoning(code: str) -> str:
    """Format a KC zoning code as 'CODE — Description'.

    Strips MHA affordability suffixes (e.g. ' (M)', ' (M1)', ' (M2)') before lookup.
    Matches by prefix so NR1/NR2/NR3 all resolve to 'Neighborhood Residential'.
    """
    base = re.sub(r"\s*\(M\d?\)\s*$", "", code.strip())
    for prefix, desc in _ZONING_DESCRIPTIONS.items():
        if base.upper().startswith(prefix):
            return f"{code} — {desc}"
    return code  # unknown code: show as-is


def load_config(config_path: str, overrides: dict) -> InvestmentConfig:
    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    with path.open() as f:
        data = yaml.safe_load(f)

    if overrides.get("market"):
        data["output"]["market"] = overrides["market"]
    if overrides.get("max_shortlist"):
        data["output"]["max_shortlist"] = overrides["max_shortlist"]

    return InvestmentConfig.model_validate(data)


def display_shortlist(shortlist: Shortlist) -> None:
    if not shortlist.deals:
        return

    console.print()
    console.print(
        f"[bold]Top {len(shortlist.deals)} deals in {shortlist.market}[/bold]\n"
    )

    for deal in shortlist.deals:
        # Build deal card header
        price_str = f"${deal.price:,.0f}"
        beds_str = f"{deal.beds}bd" if deal.beds else "?bd"
        baths_str = f"{deal.baths}ba" if deal.baths else "?ba"
        dom_str = f"{deal.days_on_market} DOM" if deal.days_on_market is not None else "? DOM"

        risk_color = {"LOW": "green", "MEDIUM": "yellow", "HIGH": "red"}.get(
            deal.risk_level, "white"
        )

        title = f"[bold cyan]#{deal.rank}[/bold cyan]  {deal.address}"
        subtitle = (
            f"{price_str}  ·  {beds_str}/{baths_str}  ·  {dom_str}  ·  "
            f"Risk: [{risk_color}]{deal.risk_level}[/{risk_color}]"
        )

        # Build metrics table
        table = Table(box=box.SIMPLE, show_header=False, padding=(0, 1))
        table.add_column(style="dim", width=22)
        table.add_column()

        if deal.cap_rate is not None:
            table.add_row("Cap Rate", f"[bold]{deal.cap_rate:.2%}[/bold]")
        if deal.coc_return is not None:
            table.add_row("Cash-on-Cash", f"[bold]{deal.coc_return:.2%}[/bold]")
        if deal.monthly_cashflow is not None:
            color = "green" if deal.monthly_cashflow >= 0 else "red"
            table.add_row(
                "Monthly Cash Flow",
                f"[{color}][bold]{deal.monthly_cashflow:+,.0f}/mo[/bold][/{color}]",
            )
        if deal.sqft is not None:
            sqft_str = f"{deal.sqft:,} sqft"
            if deal.lot_sqft is not None:
                sqft_str += f"  (lot: {deal.lot_sqft:,} sqft)"
            table.add_row("Size", sqft_str)
        if deal.home_type:
            table.add_row("Type", deal.home_type)
        if deal.school_district:
            table.add_row("School District", deal.school_district)
        if deal.hoa_fee is not None:
            table.add_row("HOA", f"${deal.hoa_fee:,.0f}/mo")
        if deal.walk_score is not None:
            table.add_row("Walk Score", str(deal.walk_score))
        if deal.flood_zone:
            table.add_row("Flood Zone", deal.flood_zone)
        if deal.zoning:
            table.add_row("Zoning", _format_zoning(deal.zoning))
        if deal.tax_assessed_value is not None:
            table.add_row("Tax Assessed Value", f"${deal.tax_assessed_value:,.0f}")

        console.print(Panel(
            "\n".join([subtitle, "", deal.narrative]),
            title=title,
            title_align="left",
            border_style="cyan" if deal.rank == 1 else "dim",
            padding=(0, 1),
        ))

        # Print metrics below the panel
        console.print(table)

        # Development potential section
        zp = deal.zoning_potential
        if zp and zp.development_score is not None:
            score = zp.development_score
            stars = "★" * score + "☆" * (5 - score)
            score_color = ["dim", "dim", "yellow", "yellow", "green", "green"][score]
            badges = []
            if zp.zoned_units and zp.zoned_units > 1:
                badges.append(f"[blue]Zoned {zp.zoned_units} units[/blue]")
            if zp.dadu_eligible:
                badges.append("[green]DADU eligible[/green]")
            if zp.adu_eligible and not zp.dadu_eligible:
                badges.append("[blue]ADU eligible[/blue]")
            if zp.subdivision_eligible:
                badges.append("[magenta]Subdivision possible[/magenta]")

            badge_str = "  ·  ".join(badges) if badges else ""
            console.print(
                f"  [bold]Development Potential[/bold]  [{score_color}]{stars}[/{score_color}]"
                + (f"  {badge_str}" if badge_str else "")
            )
            if zp.summary:
                console.print(f"  [dim]{zp.summary}[/dim]")

        console.print()

    console.print(f"[dim]{shortlist.run_summary}[/dim]\n")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Real Estate Deal Scout — find investment properties with AI"
    )
    parser.add_argument("--market", help="Target market, e.g. 'Seattle, WA'")
    parser.add_argument("--config", default="config.yaml", help="Path to config file")
    parser.add_argument("--max-shortlist", type=int, help="Max properties in shortlist")
    parser.add_argument(
        "--from-analyzed",
        metavar="FILE",
        help="Skip pipeline, re-run Claude narration on a saved analyzed JSON file",
    )
    parser.add_argument(
        "--chat",
        action="store_true",
        help="Describe your investment criteria in plain English (requires ANTHROPIC_API_KEY)",
    )
    parser.add_argument(
        "--market-refresh",
        metavar="STATE",
        help=(
            "Refresh the local area-market slice for a state (e.g. WA), then exit. "
            "Skips the ~950MB download when Redfin hasn't published new data."
        ),
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="With --market-refresh: re-download even if upstream is unchanged",
    )
    args = parser.parse_args()

    # --market-refresh: batch data refresh, no config needed
    if args.market_refresh:
        state = args.market_refresh.upper()
        console.print(f"\n[bold]Refreshing area-market data[/bold] · {state}")

        local = market_trends.local_last_modified(state)
        upstream = market_trends.upstream_last_modified()
        if local and upstream and local == upstream and not args.force:
            console.print(
                f"[green]✓[/green] Already current — Redfin last published "
                f"{upstream}.\n[dim]Use --force to re-download anyway.[/dim]"
            )
            return

        console.print(
            "[dim]Downloading ~950MB from Redfin and filtering — this takes a few "
            "minutes.[/dim]"
        )
        try:
            dest = market_trends.refresh(state=state, force=args.force)
        except Exception as e:
            console.print(f"[red]Market refresh failed: {e}[/red]")
            console.print("[dim]The previous slice (if any) is unchanged.[/dim]")
            sys.exit(1)

        rows = max(0, sum(1 for _ in dest.open(encoding="utf-8")) - 1)
        console.print(f"[green]✓[/green] {rows:,} rows written to {dest}")
        if upstream:
            console.print(f"[dim]Upstream published {upstream}[/dim]")
        return

    # --from-analyzed: re-run ranking on a previously saved analyzed JSON file
    if args.from_analyzed:
        try:
            config = load_config(
                args.config,
                {"market": args.market, "max_shortlist": args.max_shortlist},
            )
        except (FileNotFoundError, ValidationError) as e:
            console.print(f"[red]Error: {e}[/red]")
            sys.exit(1)

        console.print(f"\n[bold]Real Estate Deal Scout[/bold] · {config.output.market}\n")
        shortlist = asyncio.run(run_from_analyzed(Path(args.from_analyzed), config))
        display_shortlist(shortlist)
        return

    try:
        config = load_config(
            args.config,
            {"market": args.market, "max_shortlist": args.max_shortlist},
        )
    except FileNotFoundError as e:
        console.print(f"[red]Error: {e}[/red]")
        sys.exit(1)
    except ValidationError as e:
        console.print("[red]Config validation error:[/red]")
        for err in e.errors():
            field = ".".join(str(loc) for loc in err["loc"])
            console.print(f"  [red]{field}:[/red] {err['msg']}")
        sys.exit(1)

    if args.chat:
        updated = run_chat_intake(config)
        if updated is None:
            console.print("[yellow]Chat intake cancelled.[/yellow]")
            sys.exit(0)
        config = updated

    console.print(f"\n[bold]Real Estate Deal Scout[/bold] · {config.output.market}\n")

    shortlist = asyncio.run(run(config.output.market, config))
    display_shortlist(shortlist)


if __name__ == "__main__":
    main()
