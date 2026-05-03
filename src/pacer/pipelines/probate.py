"""Pipeline 6 — probate/estate asset sales where a domain appears in the inventory."""

from __future__ import annotations

from loguru import logger

from pacer.models.domain_candidate import DomainCandidate
from pacer.pipelines._common import upsert_candidates
from pacer.utils.api_resilience import resilient_api


@resilient_api(endpoint="probate.aggregator")
async def _fetch_probate() -> list[dict]:
    # Hook: county-level probate portals + newspaper estate-notice aggregators.
    return []


async def run_probate() -> list[DomainCandidate]:
    try:
        rows = await _fetch_probate()
    except Exception as exc:
        logger.warning("probate_fetch_failed err={}", exc)
        rows = []

    candidates: list[DomainCandidate] = []
    # TODO: map rows → candidates once feed is wired
    persisted = await upsert_candidates(candidates)
    logger.info("probate_done rows={} candidates={}", len(rows), len(persisted))
    return persisted
