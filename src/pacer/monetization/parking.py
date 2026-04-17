"""Parking & affiliate activation for caught domains below the drop-catch threshold."""

from __future__ import annotations

from loguru import logger

from pacer.config import get_settings
from pacer.models.domain_candidate import DomainCandidate, Status
from pacer.utils.api_resilience import build_client, resilient_api

settings = get_settings()


@resilient_api(endpoint="parking.activate")
async def activate_parking(candidate: DomainCandidate) -> DomainCandidate:
    provider = settings.parking_provider
    key = settings.parking_api_key.get_secret_value()
    if not key:
        logger.warning("parking_skipped_no_key domain={}", candidate.domain)
        return candidate

    base = {
        "sedo": "https://api.sedo.com",
        "bodis": "https://api.bodis.com",
        "dan": "https://api.dan.com",
    }[provider]

    async with build_client(
        base_url=base,
        headers={"Authorization": f"Bearer {key}"},
    ) as c:
        resp = await c.post(
            "/v1/domains",
            json={
                "domain": candidate.domain,
                "affiliate_tag": settings.affiliate_default_tag,
            },
        )
        resp.raise_for_status()

    candidate.monetization_strategy = f"parking:{provider}"
    candidate.status = Status.MONETIZED
    logger.info("parking_activated domain={} provider={}", candidate.domain, provider)
    return candidate
