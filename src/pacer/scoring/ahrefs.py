"""Ahrefs Site Explorer batch scoring."""

from __future__ import annotations

from dataclasses import dataclass

from loguru import logger

from pacer.config import get_settings
from pacer.utils.api_resilience import build_client, resilient_api

settings = get_settings()


@dataclass(slots=True)
class AhrefsMetrics:
    domain: str
    domain_rating: float
    backlinks: int
    referring_domains: int


@resilient_api(endpoint="ahrefs.batch_metrics", max_attempts=3)
async def batch_metrics(domains: list[str]) -> dict[str, AhrefsMetrics]:
    token = settings.ahrefs_api_token.get_secret_value()
    if not token or not domains:
        return {}

    async with build_client(
        base_url="https://api.ahrefs.com",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
    ) as c:
        resp = await c.post(
            "/v3/site-explorer/domain-rating-batch",
            json={"targets": domains, "mode": "domain"},
        )
        resp.raise_for_status()
        data = resp.json()

    out: dict[str, AhrefsMetrics] = {}
    for row in data.get("results", []):
        d = row.get("target")
        if not d:
            continue
        out[d] = AhrefsMetrics(
            domain=d,
            domain_rating=float(row.get("domain_rating") or 0),
            backlinks=int(row.get("backlinks") or 0),
            referring_domains=int(row.get("refdomains") or 0),
        )
    logger.info("ahrefs_batch size={} hits={}", len(domains), len(out))
    return out
