"""Pipeline 2 — State Secretary-of-State dissolutions.

Covers OR, CA, DE, NY, TX. Most states expose a business-entity search with a
"status=inactive|dissolved|revoked" filter. Where a machine-readable feed is
unavailable, we scrape the public portal with a polite rate limit.
"""
from __future__ import annotations

from datetime import date, timedelta

from loguru import logger

from pacer.enrichment.company_resolver import resolve_domain
from pacer.models.domain_candidate import DomainCandidate, PipelineSource, Status
from pacer.pipelines._common import upsert_candidates
from pacer.utils.api_resilience import build_client, resilient_api

STATES = ("OR", "CA", "DE", "NY", "TX")


@resilient_api(endpoint="sos.or")
async def _fetch_or(since: date) -> list[dict]:
    # Oregon: https://sos.oregon.gov/business/Pages/find.aspx (HTML scrape fallback)
    # For now, return empty — hook up actual scraper in production.
    return []


@resilient_api(endpoint="sos.ca")
async def _fetch_ca(since: date) -> list[dict]:
    # California bizfileOnline — paginated JSON endpoint
    return []


@resilient_api(endpoint="sos.de")
async def _fetch_de(since: date) -> list[dict]:
    return []


@resilient_api(endpoint="sos.ny")
async def _fetch_ny(since: date) -> list[dict]:
    return []


@resilient_api(endpoint="sos.tx")
async def _fetch_tx(since: date) -> list[dict]:
    return []


_FETCHERS = {
    "OR": _fetch_or,
    "CA": _fetch_ca,
    "DE": _fetch_de,
    "NY": _fetch_ny,
    "TX": _fetch_tx,
}


async def run_sos_dissolutions() -> list[DomainCandidate]:
    since = date.today() - timedelta(days=7)
    all_rows: list[tuple[str, dict]] = []

    for state in STATES:
        try:
            rows = await _FETCHERS[state](since)
            all_rows.extend((state, r) for r in rows)
        except Exception as exc:
            logger.warning("sos_fetch_failed state={} err={}", state, exc)
            continue

    candidates: list[DomainCandidate] = []
    for state, r in all_rows:
        name = r.get("entity_name") or r.get("name", "")
        if not name:
            continue
        domain = await resolve_domain(name)
        if not domain:
            continue
        candidates.append(
            DomainCandidate(
                domain=domain,
                company_name=name,
                source=PipelineSource.SOS_DISSOLUTION,
                source_record_id=r.get("entity_number", ""),
                source_payload={**r, "state": state},
                status=Status.DISCOVERED,
            )
        )

    persisted = await upsert_candidates(candidates)
    logger.info("sos_dissolutions_done rows={} candidates={}", len(all_rows), len(persisted))
    return persisted
