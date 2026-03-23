"""
Real Estate Deal Scout — CLI entry point.

Usage:
    scout [--market "Seattle, WA"] [--config config.yaml] [--max-shortlist 5]
    scout --from-analyzed outputs/20260320-120000_analyzed.json
"""
import argparse
import asyncio
import logging
import sys
from pathlib import Path

import yaml
from dotenv import load_dotenv
from pydantic import ValidationError
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box

from pipeline import run
from tools.models import InvestmentConfig, Shortlist

console = Console()
logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")


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

        content = f"{subtitle}\n"
        content_renderable = [table, f"\n[italic]{deal.narrative}[/italic]"]

        console.print(Panel(
            "\n".join([subtitle, "", deal.narrative]),
            title=title,
            title_align="left",
            border_style="cyan" if deal.rank == 1 else "dim",
            padding=(0, 1),
        ))

        # Print metrics below the panel
        console.print(table)
        console.print()

    console.print(f"[dim]{shortlist.run_summary}[/dim]\n")


def main() -> None:
    load_dotenv()

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
    args = parser.parse_args()

    # --from-analyzed: retry Claude narration on a previously saved file
    if args.from_analyzed:
        console.print(f"[dim]Loading analyzed data from {args.from_analyzed}...[/dim]")
        # Future: implement re-narration from saved analyzed JSON
        console.print("[red]--from-analyzed not yet implemented.[/red]")
        sys.exit(1)

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

    console.print(f"\n[bold]Real Estate Deal Scout[/bold] · {config.output.market}\n")

    shortlist = asyncio.run(run(config.output.market, config))
    display_shortlist(shortlist)


if __name__ == "__main__":
    main()
