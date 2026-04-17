"""Pipeline 5 — UCC liens & judgment distress.

Aggregates from state UCC-1 filings + CourtListener civil judgment search.
"""
from __future__ import annotations

from datetime import date, timedelta

from loguru import logger

from pacer.enrichment.company_resolver import resolve_domain
from pacer.models.domain_candidate import DomainCandidate, PipelineSource, Status
from pacer.pipelines._common import upsert_candidates
from pacer.utils.api_resilience import resilient_api


@resilient_api(endpoint="ucc.aggregate")
async def _fetch_ucc_filings(since: date) -> list[dict]:
    # Hook: Bloomberg Law, InfoTrack, or state UCC portals.
    return []


async def run_ucc_liens() -> list[DomainCandidate]:
    since = date.today() - timedelta(days=14)
    try:
        rows = await _fetch_ucc_filings(since)
    except Exception as exc:
        logger.warning("ucc_fetch_failed err={}", exc)
        rows = []

    candidates: list[DomainCandidate] = []
    for r in rows:
        name = r.get("debtor_name", "")
        if not name:
            continue
        domain = await resolve_domain(name)
        if not domain:
            continue
        candidates.append(
            DomainCandidate(
                domain=domain,
                company_name=name,
                source=PipelineSource.UCC_LIEN,
                source_record_id=r.get("filing_number", ""),
                source_payload=r,
                status=Status.DISCOVERED,
            )
        )

    persisted = await upsert_candidates(candidates)
    logger.info("ucc_done rows={} candidates={}", len(rows), len(persisted))
    return persisted
