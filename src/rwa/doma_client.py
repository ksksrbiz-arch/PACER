"""
Doma RWA tokenization client.

Mints DOT/DST tokens for high-value domains under the 1COMMERCE LLC
DFR exemption path, using the Doma REST API.
"""

import httpx
from loguru import logger

from src.config import Config
from src.models.domain import DomainCandidate
from src.utils.api_resilience import APIResilience


class DomaClient:
    @APIResilience.resilient_api_call(max_attempts=4)
    async def tokenize_domain(self, candidate: DomainCandidate) -> DomainCandidate:
        """
        Submit domain to Doma for RWA tokenization (DOT/DST minting).

        Returns candidate with rwa_token_id populated on success.
        """
        domain = candidate.domain
        if not domain:
            logger.warning(f"No domain to tokenize for {candidate.company_name!r}")
            return candidate

        payload = {
            "domain": domain,
            "entity": Config.LLC_ENTITY,
            "source": candidate.source,
            "seo_score": candidate.seo_score,
            "case_id": candidate.case_id,
        }

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{Config.DOMA_API_BASE}/tokenize",
                json=payload,
                headers={"Authorization": f"Bearer {Config.DOMA_API_KEY}"},
            )
            resp.raise_for_status()
            data = resp.json()
            candidate.rwa_token_id = data.get("token_id")
            logger.info(f"✅ Doma tokenized {domain}: token_id={candidate.rwa_token_id}")
        return candidate

    async def tokenize_batch(self, candidates: list[DomainCandidate]) -> list[DomainCandidate]:
        """Tokenize all qualified candidates via Doma."""
        return [await self.tokenize_domain(c) for c in candidates]
