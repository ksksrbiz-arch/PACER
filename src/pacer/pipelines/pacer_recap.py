"""Pipeline 1 — PACER PCL + RECAP bankruptcies → domain candidates."""

from __future__ import annotations

from loguru import logger

from pacer.enrichment.company_resolver import resolve_domain
from pacer.models.domain_candidate import DomainCandidate, PipelineSource, Status
from pacer.pacer.pacer_client import PacerClient
from pacer.pipelines._common import upsert_candidates


async def run_pacer_recap() -> list[DomainCandidate]:
    candidates: list[DomainCandidate] = []
    async with PacerClient() as client:
        filings = await client.search_recap_bankruptcies(chapters=("7", "11"))

    for f in filings:
        domain = await resolve_domain(f.debtor_name)
        if not domain:
            continue
        candidates.append(
            DomainCandidate(
                domain=domain,
                company_name=f.debtor_name,
                source=PipelineSource.PACER_RECAP,
                source_record_id=f.recap_id or f.case_number,
                source_payload=f.source_payload,
                status=Status.DISCOVERED,
            )
        )

    persisted = await upsert_candidates(candidates)
    logger.info("pacer_recap_done discovered={} persisted={}", len(candidates), len(persisted))
    return persisted
