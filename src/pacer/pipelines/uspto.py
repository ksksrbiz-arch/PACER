"""Pipeline 4 — USPTO abandoned-trademarks feed (TSDR)."""
from __future__ import annotations

from datetime import date, timedelta

from loguru import logger

from pacer.config import get_settings
from pacer.enrichment.company_resolver import resolve_domain
from pacer.models.domain_candidate import DomainCandidate, PipelineSource, Status
from pacer.pipelines._common import upsert_candidates
from pacer.utils.api_resilience import build_client, resilient_api

settings = get_settings()


@resilient_api(endpoint="uspto.abandoned")
async def _fetch_abandoned(since: date) -> list[dict]:
    async with build_client(
        base_url="https://tsdrapi.uspto.gov",
        headers={
            "USPTO-API-KEY": settings.uspto_api_key.get_secret_value(),
            "Accept": "application/json",
        },
    ) as client:
        resp = await client.get(
            "/ts/cd/casestatus/sn/search",
            params={"status": "abandoned", "from": since.isoformat()},
        )
        resp.raise_for_status()
        return resp.json().get("results", [])


async def run_uspto() -> list[DomainCandidate]:
    since = date.today() - timedelta(days=7)
    try:
        rows = await _fetch_abandoned(since)
    except Exception as exc:
        logger.warning("uspto_fetch_failed err={}", exc)
        rows = []

    candidates: list[DomainCandidate] = []
    for r in rows:
        owner = r.get("owner") or r.get("applicant", "")
        if not owner:
            continue
        domain = await resolve_domain(owner)
        if not domain:
            continue
        candidates.append(
            DomainCandidate(
                domain=domain,
                company_name=owner,
                source=PipelineSource.USPTO,
                source_record_id=str(r.get("serialNumber", "")),
                source_payload=r,
                status=Status.DISCOVERED,
            )
        )

    persisted = await upsert_candidates(candidates)
    logger.info("uspto_done rows={} candidates={}", len(rows), len(persisted))
    return persisted
