"""``pacer monetization`` CLI — smoke-tests and recent-activity ledger.

Exists so the ``docs/runbooks/flip_aftermarket_listings.md`` procedure is
actually executable. Two subcommands:

``route-one``
    Build a synthetic caught :class:`DomainCandidate` whose scoring
    profile pins the router to a specific tier, persist it, and invoke
    :meth:`MonetizationRouter.route_and_list`. The listing / Cloudflare
    calls it triggers are gated on ``Settings.aftermarket_listings_enabled``
    and ``Settings.cloudflare_api_token`` — both default to dry-run, so
    this command is safe to run in staging without keys and is the
    intended canary for post-flip smoke tests.

``list-recent``
    Dump recently-monetized candidates as JSON for diffing against a
    baseline. The runbook's staging-vs-prod acceptance check diffs a
    ``dry_run`` baseline against a live run — this command is what
    produces the right-hand side of that diff.

Usage
-----
    pacer monetization route-one --domain canary-test-123.com --tier auction_bin
    pacer monetization list-recent --since '1 hour ago' > /tmp/listings.json
    pacer monetization list-recent --since '1d' --tier 301_redirect
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import click
from loguru import logger
from sqlalchemy import and_, desc, select

from pacer.db import session_scope
from pacer.models.domain_candidate import (
    DomainCandidate,
    PipelineSource,
    Status,
)

# ─── Tier profiles ───────────────────────────────────────────────────
# Each profile pins score / domain_rating / topical_relevance / cpc_usd so
# the router's yield_score + threshold ladder falls into the requested
# tier. Keep these in sync with config defaults:
#   score_threshold_auction   = 85
#   lease_to_own_min_score    = 70 (+ commercial_component >= 50)
#   score_threshold_dropcatch = 60
#   score_threshold_parking   = 40
#
# yield_score = 0.40*DR + 0.60*(0.70*TR + 0.30*(min(CPC,20)/20 * 100))


@dataclass(frozen=True)
class TierProfile:
    score: float
    domain_rating: float
    topical_relevance: float
    cpc_usd: float
    est_monthly_searches: int


TIER_PROFILES: dict[str, TierProfile] = {
    # yield = 0.4*100 + 0.6*(0.7*100 + 0.3*100) = 40 + 60 = 100 → auction
    "auction_bin": TierProfile(
        score=99.0,
        domain_rating=100.0,
        topical_relevance=100.0,
        cpc_usd=20.0,
        est_monthly_searches=10_000,
    ),
    # yield ≈ 0.4*70 + 0.6*(0.7*70 + 0.3*25) = 28 + 33.9 = 61.9
    # Above LTO floor (70) we want ~72, so bump DR + CPC.
    "lease_to_own": TierProfile(
        score=78.0,
        domain_rating=75.0,
        topical_relevance=80.0,
        cpc_usd=6.0,
        est_monthly_searches=2_500,
    ),
    # Below auction / LTO, above dropcatch: pure 301 category redirect.
    "301_redirect": TierProfile(
        score=70.0,
        domain_rating=40.0,
        topical_relevance=40.0,
        cpc_usd=2.0,
        est_monthly_searches=500,
    ),
    # Below dropcatch, above parking: hub parking with ?ref= tag.
    "parking": TierProfile(
        score=50.0,
        domain_rating=20.0,
        topical_relevance=20.0,
        cpc_usd=0.5,
        est_monthly_searches=100,
    ),
    # Below parking: aftermarket listing (no hub target).
    "aftermarket": TierProfile(
        score=25.0,
        domain_rating=5.0,
        topical_relevance=5.0,
        cpc_usd=0.1,
        est_monthly_searches=10,
    ),
}


# ─── route-one ───────────────────────────────────────────────────────


def _build_synthetic_candidate(domain: str, tier: str) -> DomainCandidate:
    p = TIER_PROFILES[tier]
    return DomainCandidate(
        domain=domain,
        company_name=f"Canary {tier}",
        source=PipelineSource.SOS_DISSOLUTION,
        llc_entity="1COMMERCE LLC",
        status=Status.CAUGHT,
        score=p.score,
        domain_rating=p.domain_rating,
        topical_relevance=p.topical_relevance,
        cpc_usd=p.cpc_usd,
        est_monthly_searches=p.est_monthly_searches,
    )


async def _route_one(domain: str, tier: str, persist: bool) -> dict:
    # Defer import so the test suite can patch it before the call path
    # walks into Cloudflare / Afternic modules.
    from pacer.monetization.router import MonetizationRouter

    candidate = _build_synthetic_candidate(domain, tier)

    router = MonetizationRouter()
    await router.route_and_list(candidate)

    result = {
        "domain": candidate.domain,
        "requested_tier": tier,
        "resolved_strategy": candidate.monetization_strategy,
        "redirect_target": candidate.redirect_target,
        "auction_listing_url": candidate.auction_listing_url,
        "lease_monthly_price_cents": candidate.lease_monthly_price_cents,
        "lease_to_own_enabled": candidate.lease_to_own_enabled,
        "score": candidate.score,
        "persisted": False,
    }

    if persist:
        async with session_scope() as sess:
            # Upsert by domain — if canary exists from a prior run, update.
            existing = (
                await sess.execute(select(DomainCandidate).where(DomainCandidate.domain == domain))
            ).scalar_one_or_none()
            if existing is None:
                sess.add(candidate)
            else:
                existing.status = candidate.status
                existing.monetization_strategy = candidate.monetization_strategy
                existing.redirect_target = candidate.redirect_target
                existing.auction_listing_url = candidate.auction_listing_url
                existing.lease_to_own_enabled = candidate.lease_to_own_enabled
                existing.lease_monthly_price_cents = candidate.lease_monthly_price_cents
                existing.score = candidate.score
                existing.domain_rating = candidate.domain_rating
                existing.topical_relevance = candidate.topical_relevance
                existing.cpc_usd = candidate.cpc_usd
                existing.est_monthly_searches = candidate.est_monthly_searches
            await sess.commit()
        result["persisted"] = True

    return result


# ─── list-recent ─────────────────────────────────────────────────────

_SINCE_RE = re.compile(
    r"""^\s*
        (?P<amount>\d+)\s*
        (?P<unit>minutes?|mins?|m|hours?|hrs?|h|days?|d)
        (?:\s+ago)?\s*$
    """,
    re.VERBOSE | re.IGNORECASE,
)


def _parse_since(since: str) -> datetime:
    """Parse '1 hour ago', '30 min', '1d', '2 hours ago' → absolute UTC dt."""
    m = _SINCE_RE.match(since)
    if not m:
        raise click.BadParameter(f"--since must look like '1 hour ago', '30m', '2d', got {since!r}")
    amount = int(m.group("amount"))
    unit = m.group("unit").lower()
    if unit.startswith(("minute", "min", "m")) and not unit.startswith("month"):
        delta = timedelta(minutes=amount)
    elif unit.startswith(("hour", "hr", "h")):
        delta = timedelta(hours=amount)
    elif unit.startswith(("day", "d")):
        delta = timedelta(days=amount)
    else:  # pragma: no cover — regex won't match anything else
        raise click.BadParameter(f"unsupported unit: {unit}")
    return datetime.now(UTC) - delta


async def _list_recent(since: str, tier: str | None, limit: int) -> list[dict]:
    since_dt = _parse_since(since)
    async with session_scope() as sess:
        stmt = (
            select(DomainCandidate)
            .where(
                and_(
                    DomainCandidate.status == Status.MONETIZED,
                    DomainCandidate.updated_at >= since_dt,
                )
            )
            .order_by(desc(DomainCandidate.updated_at))
            .limit(limit)
        )
        if tier:
            stmt = stmt.where(DomainCandidate.monetization_strategy == tier)
        rows = list((await sess.execute(stmt)).scalars().all())

    return [
        {
            "id": r.id,
            "domain": r.domain,
            "company_name": r.company_name,
            "strategy": r.monetization_strategy,
            "redirect_target": r.redirect_target,
            "auction_listing_url": r.auction_listing_url,
            "lease_to_own_enabled": bool(r.lease_to_own_enabled),
            "lease_monthly_price_cents": r.lease_monthly_price_cents,
            "score": r.score,
            "domain_rating": r.domain_rating,
            "topical_relevance": r.topical_relevance,
            "cpc_usd": r.cpc_usd,
            "status": r.status.value if hasattr(r.status, "value") else str(r.status),
            "updated_at": r.updated_at.isoformat() if r.updated_at else None,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]


# ─── Click wiring ────────────────────────────────────────────────────


@click.group("monetization")
def cmd_monetization() -> None:
    """Monetization smoke tests and recent-activity ledger (runbook tools)."""


@cmd_monetization.command("route-one")
@click.option(
    "--domain",
    required=True,
    help="Domain to route (synthetic candidate; no actual registration).",
)
@click.option(
    "--tier",
    required=True,
    type=click.Choice(sorted(TIER_PROFILES.keys())),
    help="Force routing into this monetization tier.",
)
@click.option(
    "--no-persist",
    is_flag=True,
    default=False,
    help="Skip DB write — just call the router and print the result.",
)
def cmd_route_one(domain: str, tier: str, no_persist: bool) -> None:
    """Route a synthetic candidate into a specific tier — post-flip canary.

    Listings + Cloudflare calls are gated on their respective settings; if
    the keys aren't set you'll see status="dry_run" in the logs and the
    printed JSON will still show the strategy + target URL the router chose.
    """
    logger.info("monetization.route_one.start domain={} tier={}", domain, tier)
    result = asyncio.run(_route_one(domain, tier, persist=not no_persist))
    click.echo(json.dumps(result, indent=2, sort_keys=True))


@cmd_monetization.command("list-recent")
@click.option(
    "--since",
    required=True,
    help="How far back to look, e.g. '1 hour ago', '30m', '2 days ago', '7d'.",
)
@click.option(
    "--tier",
    default=None,
    help="Filter to a specific monetization_strategy (auction_bin, 301_redirect, ...).",
)
@click.option(
    "--limit",
    default=500,
    show_default=True,
    help="Max rows to return.",
)
def cmd_list_recent(since: str, tier: str | None, limit: int) -> None:
    """Dump recently-monetized candidates as JSON.

    Designed so you can redirect stdout to a file and diff against a
    baseline from a prior dry-run (see docs/runbooks/flip_aftermarket_listings.md).
    """
    rows = asyncio.run(_list_recent(since, tier, limit))
    json.dump(rows, sys.stdout, indent=2, sort_keys=True, default=str)
    sys.stdout.write("\n")


__all__ = [
    "cmd_monetization",
    "TIER_PROFILES",
    "TierProfile",
    "_build_synthetic_candidate",
    "_parse_since",
]
