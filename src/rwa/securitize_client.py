"""
Securitize hybrid settlement router.

Routes tokenized domains through Securitize for DFR-exempt secondary
settlement under the 1COMMERCE LLC compliance structure.
"""

import httpx
from loguru import logger

from src.config import Config
from src.models.domain import DomainCandidate
from src.utils.api_resilience import APIResilience


class SecuritizeClient:
    @APIResilience.resilient_api_call(max_attempts=4)
    async def settle(self, candidate: DomainCandidate) -> DomainCandidate:
        """
        Submit a tokenized domain asset to Securitize for hybrid settlement.

        Requires candidate.rwa_token_id to be set (from DomaClient).
        """
        if not candidate.rwa_token_id:
            logger.warning(
                f"Skipping Securitize settlement for {candidate.domain!r} " f"— no RWA token ID"
            )
            return candidate

        payload = {
            "token_id": candidate.rwa_token_id,
            "domain": candidate.domain,
            "entity": Config.LLC_ENTITY,
            "exemption": "DFR",
        }

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{Config.SECURITIZE_API_BASE}/settle",
                json=payload,
                headers={"Authorization": f"Bearer {Config.SECURITIZE_API_KEY}"},
            )
            resp.raise_for_status()
            data = resp.json()
            logger.info(
                f"✅ Securitize settled {candidate.domain}: "
                f"settlement_id={data.get('settlement_id')}"
            )
        return candidate

    async def settle_batch(self, candidates: list[DomainCandidate]) -> list[DomainCandidate]:
        """Settle all tokenized candidates via Securitize."""
        return [await self.settle(c) for c in candidates]
