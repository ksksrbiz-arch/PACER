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
    # Keys must match pacer.models.domain_candidate.Status enum values.
    return {
        "discovered": "cyan",
        "enriched": "bright_cyan",
        "scored": "green",
        "queued_dropcatch": "bright_yellow",
        "caught": "bright_green",
        "tokenized": "magenta",
        "monetized": "bold bright_green",
        "discarded": "dim",
        "failed": "red",
    }.get(status.lower(), "white")


def _strategy_colour(strategy: str | None) -> str:
    if not strategy:
        return "dim"
    return {
        "auction_bin": "bold bright_green",
        "lease_to_own": "bright_green",
        "dropcatch": "bright_yellow",
        "parking": "yellow",
        "aftermarket": "cyan",
        "discarded": "dim",
    }.get(strategy.lower(), "white")


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
    table.add_column("Strategy")
    table.add_column("Score", justify="right")
    table.add_column("DR", justify="right")
    table.add_column("Revenue", justify="right")
    table.add_column("Discovered", style="dim")

    status_counter: Counter[str] = Counter()
    total_revenue_cents = 0
    for c in rows:
        status_str = c.status.value if hasattr(c.status, "value") else str(c.status)
        status_counter[status_str] += 1
        colour = _status_colour(status_str)
        score_str = f"{c.score:.1f}" if c.score is not None else "—"
        dr_str = f"{c.domain_rating:.0f}" if c.domain_rating else "—"
        disc_str = c.discovered_at.strftime("%Y-%m-%d") if c.discovered_at else "—"

        strategy = c.monetization_strategy or ""
        strategy_colour = _strategy_colour(strategy) if strategy else "dim"
        strategy_cell = (
            f"[{strategy_colour}]{strategy}[/{strategy_colour}]" if strategy else "[dim]—[/dim]"
        )

        rev_cents = int(c.revenue_to_date_cents or 0)
        total_revenue_cents += rev_cents
        rev_cell = (
            f"[bold bright_green]${rev_cents / 100:,.2f}[/bold bright_green]"
            if rev_cents > 0
            else "[dim]—[/dim]"
        )

        table.add_row(
            c.domain,
            c.source.value if hasattr(c.source, "value") else str(c.source),
            f"[{colour}]{status_str}[/{colour}]",
            strategy_cell,
            score_str,
            dr_str,
            rev_cell,
            disc_str,
        )

    console.print(table)

    # Summary bar
    summary_parts = []
    for status, count in sorted(status_counter.items()):
        colour = _status_colour(status)
        summary_parts.append(f"[{colour}]{status}:{count}[/{colour}]")
    console.print("  " + "  ".join(summary_parts))
    if total_revenue_cents > 0:
        console.print(
            f"  [dim]Revenue on screen:[/dim] "
            f"[bold bright_green]${total_revenue_cents / 100:,.2f}[/bold bright_green]"
        )


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
    commercial_val = float(scored.topical_relevance or 0.0)

    from pacer.config import get_settings

    s = get_settings()

    # 5-tier ladder mirrors pacer.monetization.router.route_candidate():
    #   AUCTION_BIN   ≥ score_threshold_auction (default 85)
    #   LEASE_TO_OWN  ≥ lease_to_own_min_score (default 70) AND commercial ≥ 50
    #   DROPCATCH     ≥ score_threshold_dropcatch (default 60)
    #   PARKING       ≥ score_threshold_parking (default 40)
    #   AFTERMARKET   < parking threshold (discarded / no-op)
    if score_val >= s.score_threshold_auction:
        band_colour, band_label = "bold bright_green", "AUCTION BIN (list immediately)"
    elif score_val >= s.lease_to_own_min_score and commercial_val >= 50:
        band_colour, band_label = "bright_green", "LEASE-TO-OWN candidate"
    elif score_val >= s.score_threshold_dropcatch:
        band_colour, band_label = "bright_yellow", "DROP-CATCH candidate"
    elif score_val >= s.score_threshold_parking:
        band_colour, band_label = "yellow", "PARKING candidate"
    else:
        band_colour, band_label = "red", "AFTERMARKET / DISCARDED"

    details = Table.grid(padding=(0, 2))
    details.add_column(style="dim", no_wrap=True)
    details.add_column()

    details.add_row("Domain Rating (Ahrefs)", f"{scored.domain_rating or 0:.1f} / 100")
    details.add_row("Referring Domains", str(scored.referring_domains or 0))
    details.add_row("Backlinks", str(scored.backlinks or 0))
    details.add_row("Topical Relevance (LLM)", f"{commercial_val:.1f} / 100")
    details.add_row("Spam Score", f"{scored.spam_score or 0:.2f}")
    details.add_row(
        "Composite Score",
        f"[bold {band_colour}]{score_val:.2f}[/bold {band_colour}]",
    )
    details.add_row("Verdict", f"[{band_colour}]{band_label}[/{band_colour}]")
    details.add_row(
        "Thresholds",
        f"[dim]auction ≥{s.score_threshold_auction} · "
        f"LTO ≥{s.lease_to_own_min_score}+C≥50 · "
        f"drop ≥{s.score_threshold_dropcatch} · "
        f"park ≥{s.score_threshold_parking}[/dim]",
    )

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

    def _secret_set(val: Any) -> str:
        raw = val.get_secret_value() if hasattr(val, "get_secret_value") else str(val)
        return "[green]✓ set[/green]" if raw else "[dim]— not set —[/dim]"

    def _toggle(flag: bool) -> str:
        return (
            "[bold bright_green]ON[/bold bright_green]"
            if flag
            else "[bold red]OFF[/bold red]"
        )

    def _section(title: str, rows: list[tuple[str, str]]) -> Table:
        tbl = Table(title=title, box=box.SIMPLE, show_header=False, title_justify="left")
        tbl.add_column("Setting", style="cyan", no_wrap=True)
        tbl.add_column("Value")
        for key, val in rows:
            tbl.add_row(key, val)
        return tbl

    # ── Core ────────────────────────────────────────────────────────────
    core = _section(
        "Core",
        [
            ("environment", f"[bold]{s.environment}[/bold]"),
            ("llc_entity", s.llc_entity),
            ("llc_state / city", f"{s.llc_state} / {s.llc_city}"),
            ("log_level", s.log_level),
        ],
    )

    # ── LLM ─────────────────────────────────────────────────────────────
    llm = _section(
        "LLM",
        [
            ("provider", str(s.llm_provider)),
            ("anthropic_model", s.anthropic_model),
            ("ANTHROPIC_API_KEY", _secret_set(s.anthropic_api_key)),
            ("groq_model", s.groq_model),
            ("GROQ_API_KEY", _secret_set(s.groq_api_key)),
            ("OPENAI_API_KEY", _secret_set(s.openai_api_key)),
        ],
    )

    # ── Scoring / scheduler ─────────────────────────────────────────────
    scoring = _section(
        "Scoring + Scheduler",
        [
            ("AHREFS_API_TOKEN", _secret_set(s.ahrefs_api_token)),
            ("score_threshold_auction", str(s.score_threshold_auction)),
            ("lease_to_own_min_score", str(s.lease_to_own_min_score)),
            ("score_threshold_dropcatch", str(s.score_threshold_dropcatch)),
            ("score_threshold_parking", str(s.score_threshold_parking)),
            ("epmv_authority_weight", f"{s.epmv_authority_weight:.2f}"),
            ("epmv_commercial_weight", f"{s.epmv_commercial_weight:.2f}"),
            ("cron (UTC)", f"{s.schedule_cron_hour:02d}:{s.schedule_cron_minute:02d}"),
        ],
    )

    # ── Monetization + aftermarket ──────────────────────────────────────
    monetization = _section(
        "Monetization",
        [
            ("aftermarket_listings_enabled", _toggle(s.aftermarket_listings_enabled)),
            ("parking_provider", s.parking_provider),
            ("PARKING_API_KEY", _secret_set(s.parking_api_key)),
            ("AFTERNIC_API_KEY", _secret_set(s.afternic_api_key)),
            ("SEDO_SIGNKEY", _secret_set(s.sedo_signkey)),
            ("DAN_API_KEY", _secret_set(s.dan_api_key)),
            ("default_bin_price", f"${s.default_bin_price_cents / 100:,.0f}"),
            ("CLOUDFLARE_API_TOKEN", _secret_set(s.cloudflare_api_token)),
            ("cloudflare_zone_id", s.cloudflare_zone_id or "[dim]— not set —[/dim]"),
        ],
    )

    # ── Compliance (CTA/BOI + RWA) ──────────────────────────────────────
    cta_warn = ""
    if s.partner_max_rev_share_pct > 24.9:
        cta_warn = "  [bold red](CTA/BOI BREACH)[/bold red]"
    compliance = _section(
        "Compliance",
        [
            (
                "partner_max_rev_share_pct",
                f"[bold]{s.partner_max_rev_share_pct:.1f}%[/bold]{cta_warn}",
            ),
            ("partner_default_rev_share_pct", f"{s.partner_default_rev_share_pct:.1f}%"),
            ("uspto_tmscreen_enabled", _toggle(s.uspto_tmscreen_enabled)),
            ("rwa_fractional_sales_enabled", _toggle(s.rwa_fractional_sales_enabled)),
        ],
    )

    console.rule("[bold cyan]PACER — Active Configuration[/bold cyan]")
    console.print(Columns([core, llm], equal=True, expand=True))
    console.print(Columns([scoring, monetization], equal=True, expand=True))
    console.print(compliance)
    console.print("  LLM provider : ", _provider_badge(s.llm_provider))
    console.print()


# ─────────────────────────── vps link ────────────────────────────────────────

_HOSTINGER_VPS_URL = "https://hpanel.hostinger.com/vps"
_HOSTINGER_BILLING_URL = "https://hpanel.hostinger.com/billing/subscriptions"


def show_vps_link() -> None:
    """Display a direct link to the Hostinger OpenClaw VPS subscription panel."""
    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="dim", no_wrap=True)
    grid.add_column()

    grid.add_row(
        "VPS Dashboard",
        Text.from_markup(
            f"[bold cyan][link={_HOSTINGER_VPS_URL}]{_HOSTINGER_VPS_URL}[/link][/bold cyan]"
        ),
    )
    grid.add_row(
        "Subscriptions",
        Text.from_markup(
            f"[cyan][link={_HOSTINGER_BILLING_URL}]{_HOSTINGER_BILLING_URL}[/link][/cyan]"
        ),
    )
    grid.add_row("Plan", "[white]Hostinger OpenClaw VPS[/white]")
    grid.add_row("Entity", "[white]1COMMERCE LLC[/white]")

    console.print()
    console.print(
        Panel(
            grid,
            title="[bold bright_blue]☁  Hostinger OpenClaw VPS[/bold bright_blue]",
            subtitle="[dim]Ctrl+click to open in browser (terminal must support hyperlinks)[/dim]",
            border_style="bright_blue",
            padding=(1, 2),
        )
    )
    console.print()


# ─────────────────────────── deploy flow ─────────────────────────────────────

_DEPLOY_STEPS: list[tuple[str, str, str]] = [
    (
        "1",
        "Provision VPS",
        (
            "apt update && apt upgrade -y\n"
            "apt install -y docker.io docker-compose-plugin git ufw fail2ban\n"
            "systemctl enable --now docker\n"
            "ufw allow OpenSSH && ufw --force enable\n"
            "adduser --disabled-password --gecos '' pacer\n"
            "usermod -aG docker pacer\n"
            "# Copy your public SSH key for key-based login (password auth disabled):\n"
            "mkdir -p /home/pacer/.ssh && chmod 700 /home/pacer/.ssh\n"
            "echo '<YOUR_PUBLIC_KEY>' >> /home/pacer/.ssh/authorized_keys\n"
            "chmod 600 /home/pacer/.ssh/authorized_keys\n"
            "chown -R pacer:pacer /home/pacer/.ssh"
        ),
    ),
    (
        "2",
        "Clone repository",
        (
            "su - pacer\n"
            "git clone https://github.com/ksksrbiz-arch/PACER.git pacer\n"
            "cd pacer"
        ),
    ),
    (
        "3",
        "Configure secrets",
        (
            "cp .env.example .env\n"
            "# Edit .env — fill every key.\n"
            "# Generate secrets with:  openssl rand -hex 32"
        ),
    ),
    (
        "4",
        "Build & migrate",
        (
            "make deploy-prep\n"
            "# Builds Docker images and runs:  alembic upgrade head"
        ),
    ),
    (
        "5",
        "Start services",
        (
            "make docker-up\n"
            "# Starts: postgres  redis  pacer daemon"
        ),
    ),
    (
        "6",
        "Verify",
        (
            "make docker-logs\n"
            "# Scheduler fires at 03:00 UTC by default.\n"
            "# Look for:  scheduler_started  and  discovery_start"
        ),
    ),
    (
        "7",
        "Enable nightly backups",
        (
            "# Add to crontab on the VPS host:\n"
            "0 4 * * * docker exec pacer_postgres_1 pg_dump -U pacer pacer "
            "| gzip > /backups/pacer-$(date +\\%F).sql.gz"
        ),
    ),
    (
        "8",
        "Future updates",
        (
            "git pull\n"
            "make docker-down && make deploy-prep && make docker-up"
        ),
    ),
]


def show_deploy_flow() -> None:
    """Render the full VPS deployment flow as a Rich checklist."""
    console.rule("[bold cyan]PACER — Full Deployment Flow[/bold cyan]")
    console.print(
        f"  Target : [bold bright_blue][link={_HOSTINGER_VPS_URL}]"
        f"Hostinger OpenClaw VPS[/link][/bold bright_blue]"
        f"  │  Entity : [white]1COMMERCE LLC[/white]\n",
        highlight=False,
    )

    for step_num, title, commands in _DEPLOY_STEPS:
        header = Text()
        header.append(f"  Step {step_num} — ", style="dim")
        header.append(title, style="bold white")

        code_block = Table.grid(padding=(0, 1))
        code_block.add_column()
        for line in commands.splitlines():
            if line.startswith("#"):
                code_block.add_row(Text(line, style="dim italic"))
            else:
                code_block.add_row(Text(line, style="bright_yellow"))

        console.print(
            Panel(
                code_block,
                title=header,
                title_align="left",
                border_style="cyan",
                padding=(0, 1),
            )
        )

    console.print()
    console.print(
        "  [bold green]✓[/bold green]  Follow [bold]SETUP.md[/bold] §6 (DFR exemption) before "
        "enabling fractional RWA sales."
    )
    console.print(
        "  [bold green]✓[/bold green]  For monitoring details see "
        "[bold]SETUP.md[/bold] §5 (Prometheus / Slack).\n"
    )


# ─────────────────────────── health check ────────────────────────────────────


_PASS = "[bold bright_green]✓ PASS[/bold bright_green]"
_WARN = "[bold yellow]! WARN[/bold yellow]"
_FAIL = "[bold red]✗ FAIL[/bold red]"


async def _check_db() -> tuple[str, str]:
    """Return (status_markup, detail) for DB connectivity."""
    try:
        from sqlalchemy import text

        from pacer.db import session_scope

        async with session_scope() as sess:
            await sess.execute(text("SELECT 1"))
        return _PASS, "SELECT 1 OK"
    except Exception as exc:  # noqa: BLE001 — health probe surfaces any failure
        return _FAIL, f"{type(exc).__name__}: {exc}"[:120]


def _key_status(val: Any, *, required: bool) -> tuple[str, str]:
    raw = val.get_secret_value() if hasattr(val, "get_secret_value") else str(val or "")
    if raw:
        return _PASS, "set"
    return (_FAIL if required else _WARN), ("missing" if required else "not set (optional)")


async def show_health_check() -> None:
    """Operator health probe — DB, scheduler, and key inventory."""
    from pacer.config import get_settings

    s = get_settings()
    console.rule("[bold cyan]PACER — Health Check[/bold cyan]")

    # ── DB ────────────────────────────────────────────────────────────
    db_status, db_detail = await _check_db()

    # ── Scheduler ─────────────────────────────────────────────────────
    sched_ok = 0 <= s.schedule_cron_hour <= 23 and 0 <= s.schedule_cron_minute <= 59
    sched_status = _PASS if sched_ok else _FAIL
    sched_detail = (
        f"daily @ {s.schedule_cron_hour:02d}:{s.schedule_cron_minute:02d} UTC"
        if sched_ok
        else "invalid cron values"
    )

    # ── Aftermarket gate (informational) ──────────────────────────────
    if s.aftermarket_listings_enabled:
        gate_status, gate_detail = _PASS, "ON — listings will POST live"
    else:
        gate_status, gate_detail = _WARN, "OFF — dry-run mode (safe default)"

    # ── Required key set: must have at least one LLM provider key ─────
    has_llm = bool(
        s.anthropic_api_key.get_secret_value()
        or s.groq_api_key.get_secret_value()
        or s.openai_api_key.get_secret_value()
    )
    llm_status = _PASS if has_llm else _FAIL
    llm_detail = "≥1 provider key configured" if has_llm else "no LLM provider keys set"

    # ── Top-line table ────────────────────────────────────────────────
    top = Table(title="Subsystem", box=box.SIMPLE, show_header=True)
    top.add_column("Check", style="cyan", no_wrap=True)
    top.add_column("Status", justify="left")
    top.add_column("Detail", style="dim")
    top.add_row("Database", db_status, db_detail)
    top.add_row("Scheduler", sched_status, sched_detail)
    top.add_row("LLM provider", llm_status, llm_detail)
    top.add_row("Aftermarket gate", gate_status, gate_detail)
    console.print(top)

    # ── Per-service key inventory ─────────────────────────────────────
    services: list[tuple[str, list[tuple[str, Any, bool]]]] = [
        (
            "Scoring",
            [
                ("AHREFS_API_TOKEN", s.ahrefs_api_token, True),
                ("MOZ_SECRET_KEY", s.moz_secret_key, False),
                ("SEMRUSH_API_KEY", s.semrush_api_key, False),
            ],
        ),
        (
            "Discovery",
            [
                ("COURTLISTENER_API_TOKEN", s.courtlistener_api_token, False),
                ("USPTO_API_KEY", s.uspto_api_key, False),
                ("WHOISXML_API_KEY", s.whoisxml_api_key, False),
            ],
        ),
        (
            "Aftermarket",
            [
                ("AFTERNIC_API_KEY", s.afternic_api_key, s.aftermarket_listings_enabled),
                ("SEDO_SIGNKEY", s.sedo_signkey, s.aftermarket_listings_enabled),
                ("DAN_API_KEY", s.dan_api_key, False),
                ("CLOUDFLARE_API_TOKEN", s.cloudflare_api_token, False),
            ],
        ),
        (
            "Registrars",
            [
                ("DYNADOT_API_KEY", s.dynadot_api_key, False),
                ("DROPCATCH_KEY", s.dropcatch_key, False),
                ("NAMEJET_KEY", s.namejet_key, False),
                ("GODADDY_API_KEY", s.godaddy_api_key, False),
            ],
        ),
    ]

    panels: list[Panel] = []
    for service_name, keys in services:
        tbl = Table(box=box.SIMPLE, show_header=False)
        tbl.add_column("key", style="cyan", no_wrap=True)
        tbl.add_column("status")
        tbl.add_column("detail", style="dim")
        for key_name, value, required in keys:
            status_markup, detail = _key_status(value, required=required)
            tbl.add_row(key_name, status_markup, detail)
        panels.append(
            Panel(tbl, title=f"[bold]{service_name}[/bold]", border_style="cyan")
        )

    console.print(Columns(panels[:2], equal=True, expand=True))
    console.print(Columns(panels[2:], equal=True, expand=True))
    console.print()


# ─────────────────────────── monetize dry-run ───────────────────────────────


async def monetize_dry_run(domain: str, tier: str, persist: bool = False) -> None:
    """Rich-panel preview of pacer.cli.monetization.route-one."""
    from pacer.cli.monetization import TIER_PROFILES, _route_one

    if tier not in TIER_PROFILES:
        console.print(f"[bold red]Unknown tier:[/bold red] {tier}")
        console.print(
            "  Available: "
            + ", ".join(f"[cyan]{t}[/cyan]" for t in sorted(TIER_PROFILES.keys()))
        )
        return

    console.rule(f"[bold cyan]Routing preview · {domain}[/bold cyan]")
    profile = TIER_PROFILES[tier]
    inputs = Table.grid(padding=(0, 2))
    inputs.add_column(style="dim", no_wrap=True)
    inputs.add_column()
    inputs.add_row("Tier (forced)", f"[bold]{tier}[/bold]")
    inputs.add_row("Score", f"{profile.score:.1f}")
    inputs.add_row("Domain Rating", f"{profile.domain_rating:.1f}")
    inputs.add_row("Topical Relevance", f"{profile.topical_relevance:.1f}")
    inputs.add_row("CPC (USD)", f"${profile.cpc_usd:.2f}")
    inputs.add_row("Est. monthly searches", f"{profile.est_monthly_searches:,}")
    inputs.add_row("Persist to DB", _PASS if persist else _WARN + " (dry-run only)")
    console.print(Panel(inputs, title="[bold]Inputs[/bold]", border_style="cyan"))

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        TimeElapsedColumn(),
        console=console,
        transient=True,
    ) as progress:
        task = progress.add_task("[cyan]Calling MonetizationRouter…", total=None)
        result = await _route_one(domain, tier, persist=persist)
        progress.update(task, completed=1, total=1)

    strategy = result.get("resolved_strategy") or "—"
    strategy_colour = _strategy_colour(strategy)

    out = Table.grid(padding=(0, 2))
    out.add_column(style="dim", no_wrap=True)
    out.add_column()
    out.add_row(
        "Resolved strategy",
        f"[{strategy_colour}]{strategy}[/{strategy_colour}]",
    )
    out.add_row("Redirect target", result.get("redirect_target") or "[dim]—[/dim]")
    out.add_row(
        "Auction listing URL",
        result.get("auction_listing_url") or "[dim]—[/dim]",
    )
    lease_price = result.get("lease_monthly_price_cents")
    out.add_row(
        "Lease-to-own monthly",
        f"${lease_price / 100:,.2f}" if lease_price else "[dim]—[/dim]",
    )
    out.add_row(
        "LTO enabled",
        _PASS if result.get("lease_to_own_enabled") else "[dim]—[/dim]",
    )
    out.add_row("Persisted", _PASS if result.get("persisted") else _WARN + " (skipped)")

    console.print(
        Panel(
            out,
            title=f"[bold {strategy_colour}]Router decision[/bold {strategy_colour}]",
            border_style=strategy_colour,
        )
    )


# ─────────────────────────── partners ledger ────────────────────────────────


async def show_partners_summary() -> None:
    """Roster + YTD payout summary + 1099-NEC + CTA/BOI compliance flags."""
    from datetime import date

    from sqlalchemy import func, select

    from pacer.config import get_settings
    from pacer.db import session_scope
    from pacer.partners.ledger import PayoutEntry, PayoutStatus
    from pacer.partners.models.partner import Partner, PartnerStatus

    s = get_settings()
    cap = s.partner_max_rev_share_pct
    ytd_start = date(date.today().year, 1, 1)

    async with session_scope() as sess:
        partners = list(
            (
                await sess.execute(
                    select(Partner).order_by(Partner.legal_name.asc())
                )
            ).scalars().all()
        )

        # Aggregate YTD totals per partner / status.
        ytd_stmt = (
            select(
                PayoutEntry.partner_id,
                PayoutEntry.status,
                func.coalesce(func.sum(PayoutEntry.partner_cents), 0).label("cents"),
            )
            .where(PayoutEntry.period_start >= ytd_start)
            .group_by(PayoutEntry.partner_id, PayoutEntry.status)
        )
        agg = (await sess.execute(ytd_stmt)).all()

    # Index aggregates by (partner_id, status).
    by_partner: dict[int, dict[str, int]] = {}
    for partner_id, status, cents in agg:
        key = status.value if hasattr(status, "value") else str(status)
        by_partner.setdefault(partner_id, {})[key] = int(cents or 0)

    console.rule("[bold cyan]PACER — Partner Ledger[/bold cyan]")
    console.print(
        f"  YTD window: [dim]{ytd_start.isoformat()} → today[/dim]   "
        f"CTA/BOI cap: [bold]{cap:.1f}%[/bold]   "
        f"1099-NEC threshold: [bold]$600[/bold]"
    )

    if not partners:
        console.print("[yellow]  No partners configured.[/yellow]\n")
        return

    table = Table(box=box.ROUNDED, show_lines=False)
    table.add_column("Partner", style="cyan", no_wrap=True)
    table.add_column("Status", justify="left")
    table.add_column("Share %", justify="right")
    table.add_column("YTD Pending", justify="right")
    table.add_column("YTD Paid", justify="right")
    table.add_column("YTD Total", justify="right")
    table.add_column("W-9", justify="center")
    table.add_column("1099-NEC", justify="center")
    table.add_column("CTA/BOI", justify="center")

    breaches = 0
    grand_pending = 0
    grand_paid = 0
    for p in partners:
        amounts = by_partner.get(p.id, {})
        pending = amounts.get(PayoutStatus.PENDING.value, 0)
        paid = amounts.get(PayoutStatus.PAID.value, 0)
        total = pending + paid
        grand_pending += pending
        grand_paid += paid

        # Status colour
        status_str = p.status.value if hasattr(p.status, "value") else str(p.status)
        status_colours = {
            PartnerStatus.ACTIVE.value: "bright_green",
            PartnerStatus.PROSPECT.value: "cyan",
            PartnerStatus.PAUSED.value: "yellow",
            PartnerStatus.TERMINATED.value: "dim",
        }
        scol = status_colours.get(status_str, "white")

        share_cell = f"{p.rev_share_pct:.1f}%"
        cta_breach = p.rev_share_pct > cap
        if cta_breach:
            breaches += 1
            cta_cell = "[bold red]✗ BREACH[/bold red]"
            share_cell = f"[bold red]{share_cell}[/bold red]"
        else:
            cta_cell = "[bright_green]✓[/bright_green]"

        nec_required = total >= 60_000  # $600 in cents
        nec_cell = (
            "[bold yellow]REQUIRED[/bold yellow]"
            if nec_required
            else "[dim]—[/dim]"
        )

        w9_cell = (
            "[bright_green]✓[/bright_green]"
            if p.w9_received
            else ("[bold red]✗[/bold red]" if nec_required else "[dim]—[/dim]")
        )

        def _money(c: int) -> str:
            return f"${c / 100:,.2f}" if c else "[dim]—[/dim]"

        table.add_row(
            p.display_name or p.legal_name,
            f"[{scol}]{status_str}[/{scol}]",
            share_cell,
            _money(pending),
            _money(paid),
            f"[bold]{_money(total)}[/bold]" if total else _money(total),
            w9_cell,
            nec_cell,
            cta_cell,
        )

    console.print(table)
    grand_total = grand_pending + grand_paid
    console.print(
        f"  Roster total YTD: pending [bold yellow]${grand_pending / 100:,.2f}[/bold yellow]   "
        f"paid [bold bright_green]${grand_paid / 100:,.2f}[/bold bright_green]   "
        f"all [bold]${grand_total / 100:,.2f}[/bold]"
    )
    if breaches:
        console.print(
            f"  [bold red]⚠  {breaches} partner(s) over CTA/BOI cap "
            f"({cap:.1f}%) — fix before next payout cycle.[/bold red]"
        )
    console.print()
