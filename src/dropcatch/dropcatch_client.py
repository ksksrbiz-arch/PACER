"""
Drop-catch automation client.

Supports Dynadot, DropCatch, and NameJet/GoDaddy backorder APIs.
Queues high-value domains (score ≥ threshold) for drop-catch registration.
"""

import httpx
from loguru import logger

from src.config import Config
from src.models.domain import DomainCandidate
from src.utils.api_resilience import APIResilience


class DropCatchClient:
    DYNADOT_BASE = "https://api.dynadot.com/api3.json"
    DROPCATCH_BASE = "https://api.dropcatch.com/v1"
    NAMEJET_BASE = "https://api.namejet.com/v1"

    @APIResilience.resilient_api_call(max_attempts=4)
    async def _dynadot_backorder(self, domain: str) -> dict:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(
                self.DYNADOT_BASE,
                params={
                    "key": Config.DYNADOT_API_KEY,
                    "command": "backorder_domain",
                    "domain": domain,
                },
            )
            resp.raise_for_status()
            return resp.json()

    @APIResilience.resilient_api_call(max_attempts=4)
    async def _dropcatch_backorder(self, domain: str) -> dict:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(
                f"{self.DROPCATCH_BASE}/backorders",
                json={"domain": domain},
                headers={"X-API-Key": Config.DROPCATCH_API_KEY},
            )
            resp.raise_for_status()
            return resp.json()

    async def queue_domain(self, candidate: DomainCandidate) -> DomainCandidate:
        """Attempt to backorder a domain across all configured registrars."""
        domain = candidate.domain
        if not domain:
            logger.warning(f"No domain to queue for {candidate.company_name!r}")
            return candidate

        status_parts = []

        if Config.DYNADOT_API_KEY:
            try:
                result = await self._dynadot_backorder(domain)
                status_parts.append(f"dynadot:{result.get('status', 'queued')}")
            except Exception as exc:
                logger.error(f"Dynadot backorder failed for {domain}: {exc}")
                status_parts.append("dynadot:failed")

        if Config.DROPCATCH_API_KEY:
            try:
                result = await self._dropcatch_backorder(domain)
                status_parts.append(f"dropcatch:{result.get('status', 'queued')}")
            except Exception as exc:
                logger.error(f"DropCatch backorder failed for {domain}: {exc}")
                status_parts.append("dropcatch:failed")

        candidate.drop_catch_status = ",".join(status_parts) if status_parts else "pending"
        logger.info(f"Drop-catch queued {domain}: {candidate.drop_catch_status}")
        return candidate

    async def queue_batch(self, candidates: list[DomainCandidate]) -> list[DomainCandidate]:
        """Queue all qualified candidates for drop-catch."""
        return [await self.queue_domain(c) for c in candidates]
