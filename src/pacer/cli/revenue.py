"""``pacer revenue`` CLI — tier-1 data-licensing feed exports.

Initial bolt-on extension:
    Export recent distressed-domain signals as JSON for downstream buyers
    (research desks, brokers, legal analysts, and data partners).
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
from datetime import UTC, datetime, timedelta

import click
from sqlalchemy import and_, desc, select

from pacer.db import session_scope
from pacer.models.domain_candidate import DomainCandidate, PipelineSource, Status

_SINCE_RE = re.compile(
    r"""^\s*
        (?P<amount>\d+)\s*
        (?P<unit>minutes?|mins?|m|hours?|hrs?|h|days?|d)
        (?:\s+ago)?\s*$
    """,
    re.VERBOSE | re.IGNORECASE,
)


def _parse_since(since: str) -> datetime:
    m = _SINCE_RE.match(since)
    if not m:
        raise click.BadParameter(f"--since must look like '1 hour ago', '30m', '2d', got {since!r}")

    amount = int(m.group("amount"))
    unit = m.group("unit").lower()
    minute_units = {"m", "min", "mins", "minute", "minutes"}
    hour_units = {"h", "hr", "hrs", "hour", "hours"}
    day_units = {"d", "day", "days"}

    if unit in minute_units:
        delta = timedelta(minutes=amount)
    elif unit in hour_units:
        delta = timedelta(hours=amount)
    elif unit in day_units:
        delta = timedelta(days=amount)
    else:
        raise click.BadParameter(f"unsupported unit: {unit}")
    return datetime.now(UTC) - delta


async def _list_signals(
    since: str,
    source: str | None,
    status: str | None,
    min_score: float | None,
    limit: int,
) -> list[dict]:
    since_dt = _parse_since(since)
    async with session_scope() as sess:
        stmt = (
            select(DomainCandidate)
            .where(DomainCandidate.updated_at >= since_dt)
            .order_by(desc(DomainCandidate.updated_at))
            .limit(limit)
        )

        filters = []
        if source:
            filters.append(DomainCandidate.source == PipelineSource(source))
        if status:
            filters.append(DomainCandidate.status == Status(status))
        if min_score is not None:
            filters.append(DomainCandidate.score.is_not(None))
            filters.append(DomainCandidate.score >= float(min_score))
        if filters:
            stmt = stmt.where(and_(*filters))

        rows = list((await sess.execute(stmt)).scalars().all())

    return [
        {
            "id": r.id,
            "domain": r.domain,
            "company_name": r.company_name,
            "source": r.source.value,
            "status": r.status.value,
            "score": r.score,
            "domain_rating": r.domain_rating,
            "backlinks": r.backlinks,
            "referring_domains": r.referring_domains,
            "topical_relevance": r.topical_relevance,
            "spam_score": r.spam_score,
            "pending_delete_date": (
                r.pending_delete_date.isoformat() if r.pending_delete_date else None
            ),
            "source_record_id": r.source_record_id,
            "updated_at": r.updated_at.isoformat() if r.updated_at else None,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "llc_entity": r.llc_entity,
        }
        for r in rows
    ]


@click.group("revenue")
def cmd_revenue() -> None:
    """Tier-1 revenue surfaces (starting with data-feed exports)."""


@cmd_revenue.command("list-signals")
@click.option(
    "--since",
    default="24h",
    show_default=True,
    help="Lookback window, e.g. '1h', '24h', '7d'.",
)
@click.option(
    "--source",
    default=None,
    type=click.Choice([s.value for s in PipelineSource]),
    help="Filter to one pipeline source.",
)
@click.option(
    "--status",
    default=None,
    type=click.Choice([s.value for s in Status]),
    help="Filter to one lifecycle status.",
)
@click.option(
    "--min-score",
    type=float,
    default=None,
    help="Include only rows with score >= this value.",
)
@click.option("--limit", default=500, show_default=True, help="Max rows to return.")
def cmd_list_signals(
    since: str, source: str | None, status: str | None, min_score: float | None, limit: int
) -> None:
    """Dump recent distress signals as JSON for B2B feed consumers."""
    rows = asyncio.run(_list_signals(since, source, status, min_score, limit))
    json.dump(rows, sys.stdout, indent=2, sort_keys=True, default=str)
    sys.stdout.write("\n")


__all__ = ["cmd_revenue", "_list_signals", "_parse_since"]
