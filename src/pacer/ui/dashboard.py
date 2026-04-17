"""Rich-powered developer dashboard for PACER.

Provides helpers consumed by the ``pacer dev`` Click command group:
    - run_pipeline_live()   — run the full daily cycle with a live progress bar
    - show_status_table()   — tabular view of domain candidates by status/source
    - score_domain_live()   — score a single domain and print a breakdown panel
    - show_config_summary() — display active settings (no secret values)

All output goes through Rich so it renders cleanly in any modern terminal.
"""
from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from typing import Any

from rich import box
from rich.columns import Columns
from rich.console import Console
from rich.panel import Panel
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.table import Table
from rich.text import Text

console = Console()


# ─────────────────────────── helpers ────────────────────────────────────────


def _provider_badge(provider: str) -> Text:
    colours = {"claude": "bright_magenta", "groq": "bright_green", "openai": "bright_yellow"}
    colour = colours.get(provider, "white")
    return Text(f"● {provider}", style=colour)


def _status_colour(status: str) -> str:
    return {
        "discovered": "cyan",
        "scored": "green",
        "dropcatch_queued": "bright_yellow",
        "dropcatch_won": "bright_green",
        "dropcatch_failed": "red",
        "parking_active": "yellow",
        "discarded": "dim",
    }.get(status.lower(), "white")


# ─────────────────────────── run pipeline ────────────────────────────────────


async def run_pipeline_live() -> dict[str, Any]:
    """Run the full daily cycle and show live Rich progress."""
    from pacer.main import run_daily

    console.rule("[bold cyan]PACER — Developer Run[/bold cyan]")
    console.print(
        f"  Started : [dim]{datetime.now(UTC).isoformat(timespec='seconds')}[/dim]",
        highlight=False,
    )

    report: dict[str, Any] = {}

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TimeElapsedColumn(),
        console=console,
        transient=False,
    ) as progress:
        task = progress.add_task("[cyan]Running pipeline…", total=None)
        report = await run_daily()
        progress.update(task, description="[green]Pipeline complete ✓", completed=1, total=1)

    _render_report(report)
    return report


def _render_report(report: dict[str, Any]) -> None:
    discovery = report.get("discovery", {})
    routing = report.get("routing", {})
    scored = report.get("scored", 0)

    # Discovery table
    disc_table = Table(title="Discovery", box=box.SIMPLE, show_header=True)
    disc_table.add_column("Pipeline", style="cyan")
    disc_table.add_column("Result", justify="right")
    for name, val in discovery.items():
        colour = "red" if isinstance(val, str) and val.startswith("error") else "green"
        disc_table.add_row(name.replace("run_", ""), f"[{colour}]{val}[/{colour}]")

    # Routing summary
    route_table = Table(title="Routing", box=box.SIMPLE, show_header=True)
    route_table.add_column("Action", style="yellow")
    route_table.add_column("Count", justify="right")
    for key, val in routing.items():
        route_table.add_row(key, str(val))

    console.print()
    console.print(Columns([disc_table, route_table]))
    console.print(f"\n  Scored this run : [bold green]{scored}[/bold green]")
    console.print(
        f"  Finished at     : [dim]{report.get('finished_at', '?')}[/dim]",
        highlight=False,
    )


# ─────────────────────────── status table ────────────────────────────────────


async def show_status_table(limit: int = 50) -> None:
    """Print a Rich table of recent domain candidates from the DB."""
    from sqlalchemy import select

    from pacer.db import session_scope
    from pacer.models.domain_candidate import DomainCandidate

    async with session_scope() as sess:
        stmt = (
            select(DomainCandidate)
            .order_by(DomainCandidate.discovered_at.desc())
            .limit(limit)
        )
        rows = list((await sess.execute(stmt)).scalars().all())

    if not rows:
        console.print("[yellow]No candidates found in the database.[/yellow]")
        return

    table = Table(
        title=f"Domain Candidates (latest {limit})",
        box=box.ROUNDED,
        show_lines=False,
    )
    table.add_column("Domain", style="cyan", no_wrap=True)
    table.add_column("Source", style="dim")
    table.add_column("Status")
    table.add_column("Score", justify="right")
    table.add_column("DR", justify="right")
    table.add_column("Discovered", style="dim")

    status_counter: Counter[str] = Counter()
    for c in rows:
        status_str = c.status.value if hasattr(c.status, "value") else str(c.status)
        status_counter[status_str] += 1
        colour = _status_colour(status_str)
        score_str = f"{c.score:.1f}" if c.score is not None else "—"
        dr_str = f"{c.domain_rating:.0f}" if c.domain_rating else "—"
        disc_str = c.discovered_at.strftime("%Y-%m-%d") if c.discovered_at else "—"
        table.add_row(
            c.domain,
            c.source.value if hasattr(c.source, "value") else str(c.source),
            f"[{colour}]{status_str}[/{colour}]",
            score_str,
            dr_str,
            disc_str,
        )

    console.print(table)

    # Summary bar
    summary_parts = []
    for status, count in sorted(status_counter.items()):
        colour = _status_colour(status)
        summary_parts.append(f"[{colour}]{status}:{count}[/{colour}]")
    console.print("  " + "  ".join(summary_parts))


# ─────────────────────────── score single domain ─────────────────────────────


async def score_domain_live(domain: str, company: str | None = None) -> None:
    """Score a single domain and render a detailed breakdown panel."""
    from pacer.models.domain_candidate import DomainCandidate, PipelineSource, Status
    from pacer.scoring.engine import score_candidate

    console.rule(f"[bold]Scoring: {domain}[/bold]")

    candidate = DomainCandidate(
        domain=domain,
        source=PipelineSource.EDGAR,  # sentinel for manual scoring
        status=Status.DISCOVERED,
        company_name=company,
    )

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        TimeElapsedColumn(),
        console=console,
        transient=True,
    ) as progress:
        task = progress.add_task("[cyan]Fetching Ahrefs + LLM…", total=None)
        scored = await score_candidate(candidate)
        progress.update(task, completed=1, total=1)

    score_val = scored.score or 0.0

    # Score band
    if score_val >= 60:
        band_colour, band_label = "bright_green", "DROP-CATCH candidate"
    elif score_val >= 40:
        band_colour, band_label = "yellow", "PARKING candidate"
    else:
        band_colour, band_label = "red", "DISCARDED"

    details = Table.grid(padding=(0, 2))
    details.add_column(style="dim", no_wrap=True)
    details.add_column()

    details.add_row("Domain Rating (Ahrefs)", f"{scored.domain_rating or 0:.1f} / 100")
    details.add_row("Referring Domains", str(scored.referring_domains or 0))
    details.add_row("Backlinks", str(scored.backlinks or 0))
    details.add_row("Topical Relevance (LLM)", f"{scored.topical_relevance or 0:.1f} / 100")
    details.add_row("Spam Score", f"{scored.spam_score or 0:.2f}")
    details.add_row(
        "Composite Score",
        f"[bold {band_colour}]{score_val:.2f}[/bold {band_colour}]",
    )
    details.add_row("Verdict", f"[{band_colour}]{band_label}[/{band_colour}]")

    console.print(
        Panel(
            details,
            title=f"[bold]{domain}[/bold]",
            subtitle=f"[dim]{company or ''}[/dim]",
            border_style=band_colour,
        )
    )


# ─────────────────────────── config summary ──────────────────────────────────


def show_config_summary() -> None:
    """Print active settings without exposing secret values."""
    from pacer.config import get_settings

    s = get_settings()

    table = Table(title="Active Configuration", box=box.SIMPLE, show_header=True)
    table.add_column("Setting", style="cyan", no_wrap=True)
    table.add_column("Value")

    def _secret_set(val: Any) -> str:
        raw = val.get_secret_value() if hasattr(val, "get_secret_value") else str(val)
        return "[green]✓ set[/green]" if raw else "[dim]— not set —[/dim]"

    rows = [
        ("environment", s.environment),
        ("llc_entity", s.llc_entity),
        ("llm_provider", str(s.llm_provider)),
        ("anthropic_model", s.anthropic_model),
        ("ANTHROPIC_API_KEY", _secret_set(s.anthropic_api_key)),
        ("groq_model", s.groq_model),
        ("GROQ_API_KEY", _secret_set(s.groq_api_key)),
        ("OPENAI_API_KEY", _secret_set(s.openai_api_key)),
        ("AHREFS_API_TOKEN", _secret_set(s.ahrefs_api_token)),
        ("score_threshold_dropcatch", str(s.score_threshold_dropcatch)),
        ("score_threshold_parking", str(s.score_threshold_parking)),
        ("schedule_cron_hour", str(s.schedule_cron_hour)),
        ("schedule_cron_minute", str(s.schedule_cron_minute)),
        ("log_level", s.log_level),
    ]
    for key, val in rows:
        table.add_row(key, val)

    llm_col = _provider_badge(s.llm_provider)
    console.print(table)
    console.print("  LLM provider : ", llm_col)
    console.print()
