"""PACER orchestrator — daily cron + one-shot CLI.

Runs the full Foundation → Revenue flow:
    1. Fan-out 6 discovery pipelines concurrently
    2. Score all newly-discovered candidates
    3. Route by score:
         score >= dropcatch_threshold   → multi-registrar backorders
         parking_threshold <= score < dropcatch_threshold → parking/affiliate
         score < parking_threshold      → discard
    4. Post Slack summary alert

Usage
-----
    poetry run pacer run-once           # execute the daily flow immediately
    poetry run pacer schedule           # start APScheduler and block
    poetry run pacer status             # print pipeline/state counts
"""
from __future__ import annotations

import asyncio
import sys
from collections import Counter
from datetime import UTC, datetime

import click
import httpx
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from loguru import logger
from sqlalchemy import func, select

from pacer.compliance.audit import record_event
from pacer.config import get_settings
from pacer.db import session_scope
from pacer.dropcatch.orchestrator import submit_backorders
from pacer.models.domain_candidate import DomainCandidate, Status
from pacer.monetization.parking import activate_parking
from pacer.pipelines import ALL_PIPELINES
from pacer.scoring.engine import score_candidates

settings = get_settings()


# ─────────────────────────── logging ────────────────────────────
def _configure_logging() -> None:
    logger.remove()
    logger.add(
        sys.stderr,
        level=settings.log_level,
        backtrace=False,
        diagnose=False,
        format=(
            "<green>{time:YYYY-MM-DDTHH:mm:ssZ}</green> "
            "<level>{level:<7}</level> "
            "<cyan>{name}</cyan>:{function}:{line} - {message}"
        ),
    )


# ─────────────────────────── discovery ──────────────────────────
async def _run_discovery() -> dict[str, int | str]:
    """Fan out all 6 pipelines. Each returns a count or raises."""
    logger.info("discovery_start pipelines={}", len(ALL_PIPELINES))
    tasks = [p() for p in ALL_PIPELINES]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    summary: dict[str, int | str] = {}
    for pipeline, result in zip(ALL_PIPELINES, results, strict=True):
        name = pipeline.__name__
        if isinstance(result, Exception):
            logger.error("pipeline_failed name={} err={}", name, result)
            summary[name] = f"error:{type(result).__name__}"
        else:
            count = int(result) if isinstance(result, int) else 0
            summary[name] = count
    return summary


# ─────────────────────────── routing ────────────────────────────
async def _route_by_score() -> dict[str, int]:
    """Pull newly-scored candidates and dispatch them by score band."""
    dropcatch_thr = settings.score_threshold_dropcatch
    parking_thr = settings.score_threshold_parking

    async with session_scope() as sess:
        stmt = select(DomainCandidate).where(DomainCandidate.status == Status.SCORED)
        scored: list[DomainCandidate] = list((await sess.execute(stmt)).scalars().all())

    high = [c for c in scored if (c.score or 0) >= dropcatch_thr]
    mid = [c for c in scored if parking_thr <= (c.score or 0) < dropcatch_thr]
    low = [c for c in scored if (c.score or 0) < parking_thr]

    # Drop-catch fan-out
    dc_tasks = [submit_backorders(c) for c in high]
    dc_results = await asyncio.gather(*dc_tasks, return_exceptions=True)
    dc_ok = sum(1 for r in dc_results if not isinstance(r, Exception))

    # Parking / affiliate
    park_tasks = [activate_parking(c) for c in mid]
    park_results = await asyncio.gather(*park_tasks, return_exceptions=True)
    park_ok = sum(1 for r in park_results if not isinstance(r, Exception))

    # Discard low-score candidates
    if low:
        async with session_scope() as sess:
            for c in low:
                existing = (
                    await sess.execute(
                        select(DomainCandidate).where(DomainCandidate.domain == c.domain)
                    )
                ).scalar_one_or_none()
                if existing is not None:
                    existing.status = Status.DISCARDED

    return {
        "dropcatch_queued": dc_ok,
        "dropcatch_failed": len(high) - dc_ok,
        "parking_activated": park_ok,
        "parking_failed": len(mid) - park_ok,
        "discarded": len(low),
    }


# ─────────────────────────── scoring stage ──────────────────────
async def _run_scoring() -> int:
    async with session_scope() as sess:
        stmt = select(DomainCandidate).where(DomainCandidate.status == Status.DISCOVERED)
        fresh: list[DomainCandidate] = list((await sess.execute(stmt)).scalars().all())

    if not fresh:
        logger.info("scoring_skip reason=no_fresh_candidates")
        return 0
    logger.info("scoring_start count={}", len(fresh))
    await score_candidates(fresh)
    return len(fresh)


# ─────────────────────────── alert ──────────────────────────────
async def _send_slack_summary(report: dict) -> None:
    webhook = settings.slack_webhook_url.get_secret_value()
    if not webhook:
        logger.debug("slack_summary_skipped reason=no_webhook")
        return

    text = (
        f":brick: *PACER daily run* — {datetime.now(UTC).isoformat(timespec='seconds')}\n"
        f"*Environment:* `{settings.environment}` | *LLC:* `{settings.llc_entity}`\n"
        f"*Discovery:* {report.get('discovery')}\n"
        f"*Scored:* {report.get('scored', 0)}\n"
        f"*Routing:* {report.get('routing')}\n"
    )
    try:
        async with httpx.AsyncClient(timeout=10.0) as c:
            await c.post(webhook, json={"text": text, "channel": settings.alert_channel})
    except Exception as e:  # pragma: no cover - alerting must not break the run
        logger.warning("slack_alert_failed err={}", e)


# ─────────────────────────── main flow ──────────────────────────
async def run_daily() -> dict:
    """One full Foundation → Revenue cycle."""
    started = datetime.now(UTC)
    await record_event(
        event_type="daily_run_started",
        endpoint="main.run_daily",
        message=f"env={settings.environment}",
    )

    discovery = await _run_discovery()
    scored = await _run_scoring()
    routing = await _route_by_score()

    report = {
        "started_at": started.isoformat(),
        "finished_at": datetime.now(UTC).isoformat(),
        "discovery": discovery,
        "scored": scored,
        "routing": routing,
    }
    logger.info("daily_run_complete report={}", report)

    await record_event(
        event_type="daily_run_completed",
        endpoint="main.run_daily",
        message="ok",
        payload=report,
    )
    await _send_slack_summary(report)
    return report


async def print_status() -> None:
    async with session_scope() as sess:
        by_status = (
            await sess.execute(
                select(DomainCandidate.status, func.count()).group_by(DomainCandidate.status)
            )
        ).all()
        by_source = (
            await sess.execute(
                select(DomainCandidate.source, func.count()).group_by(DomainCandidate.source)
            )
        ).all()

    status_counts = Counter({str(s.value): n for s, n in by_status})
    source_counts = Counter({str(s.value): n for s, n in by_source})
    logger.info("status_counts {}", dict(status_counts))
    logger.info("source_counts {}", dict(source_counts))


# ─────────────────────────── scheduler ──────────────────────────
async def _run_scheduler() -> None:
    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(
        run_daily,
        CronTrigger(hour=settings.schedule_cron_hour, minute=settings.schedule_cron_minute),
        id="pacer_daily",
        name="PACER daily discovery+scoring+routing",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=60 * 30,
    )
    scheduler.start()
    logger.info(
        "scheduler_started cron=0{}:{:02d} UTC",
        settings.schedule_cron_hour,
        settings.schedule_cron_minute,
    )
    try:
        # Block forever
        while True:
            await asyncio.sleep(3600)
    except (KeyboardInterrupt, asyncio.CancelledError):  # pragma: no cover
        scheduler.shutdown(wait=False)
        logger.info("scheduler_stopped")


# ─────────────────────────── CLI ────────────────────────────────
@click.group()
def cli() -> None:
    """PACER — distressed-domain arbitrage + RWA tokenization."""
    _configure_logging()


@cli.command("run-once")
def cmd_run_once() -> None:
    """Execute a single daily run right now."""
    asyncio.run(run_daily())


@cli.command("schedule")
def cmd_schedule() -> None:
    """Start the APScheduler daemon and block."""
    asyncio.run(_run_scheduler())


@cli.command("status")
def cmd_status() -> None:
    """Print pipeline/state counts from the database."""
    asyncio.run(print_status())


@cli.command("version")
def cmd_version() -> None:
    from importlib.metadata import PackageNotFoundError, version

    try:
        click.echo(version("pacer"))
    except PackageNotFoundError:
        click.echo("pacer (dev)")



# ─────────────────────────── developer UI ────────────────────────────────
@cli.group("dev")
def cmd_dev() -> None:
    """Developer tools: rich terminal UI, single-domain scoring, config check."""


@cmd_dev.command("run")
def cmd_dev_run() -> None:
    """Run the full pipeline with a Rich live dashboard."""
    from pacer.ui.dashboard import run_pipeline_live

    asyncio.run(run_pipeline_live())


@cmd_dev.command("status")
@click.option("--limit", default=50, show_default=True, help="Max rows to display.")
def cmd_dev_status(limit: int) -> None:
    """Show domain candidates in a Rich table (latest N rows)."""
    from pacer.ui.dashboard import show_status_table

    asyncio.run(show_status_table(limit=limit))


@cmd_dev.command("score")
@click.argument("domain")
@click.option("--company", default=None, help="Company name hint for the LLM.")
def cmd_dev_score(domain: str, company: str | None) -> None:
    """Score a single DOMAIN and display a detailed breakdown panel."""
    from pacer.ui.dashboard import score_domain_live

    asyncio.run(score_domain_live(domain, company))


@cmd_dev.command("config")
def cmd_dev_config() -> None:
    """Print active settings (secrets redacted)."""
    from pacer.ui.dashboard import show_config_summary

    show_config_summary()

if __name__ == "__main__":  # pragma: no cover
    cli()
