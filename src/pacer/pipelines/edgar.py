"""Pipeline 3 — EDGAR distressed filings.

Scans recent 8-K (bankruptcy, delisting), 15-12B (deregistration), and NT filings.
"""
from __future__ import annotations

from datetime import date, timedelta

from loguru import logger

from pacer.config import get_settings
from pacer.enrichment.company_resolver import resolve_domain
from pacer.models.domain_candidate import DomainCandidate, PipelineSource, Status
from pacer.pipelines._common import upsert_candidates
from pacer.utils.api_resilience import build_client, resilient_api

settings = get_settings()

DISTRESS_FORMS = ("8-K", "15-12B", "15-12G", "NT 10-K", "NT 10-Q")


@resilient_api(endpoint="edgar.recent")
async def _fetch_recent(since: date) -> list[dict]:
    async with build_client(
        base_url="https://data.sec.gov",
        headers={"User-Agent": settings.sec_user_agent, "Accept": "application/json"},
    ) as client:
        out: list[dict] = []
        for form in DISTRESS_FORMS:
            resp = await client.get(
                "/submissions/CIK_recent.json",
                params={"form": form, "since": since.isoformat()},
            )
            if resp.status_code == 404:
                continue
            resp.raise_for_status()
            out.extend(resp.json().get("filings", []))
        return out


async def run_edgar() -> list[DomainCandidate]:
    since = date.today() - timedelta(days=2)
    try:
        filings = await _fetch_recent(since)
    except Exception as exc:
        logger.warning("edgar_fetch_failed err={}", exc)
        filings = []

    candidates: list[DomainCandidate] = []
    for f in filings:
        name = f.get("companyName") or f.get("entityName", "")
        if not name:
            continue
        domain = await resolve_domain(name)
        if not domain:
            continue
        candidates.append(
            DomainCandidate(
                domain=domain,
                company_name=name,
                source=PipelineSource.EDGAR,
                source_record_id=f.get("accessionNumber", ""),
                source_payload=f,
                status=Status.DISCOVERED,
            )
        )

    persisted = await upsert_candidates(candidates)
    logger.info("edgar_done filings={} candidates={}", len(filings), len(persisted))
    return persisted
