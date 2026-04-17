"""Hybrid settlement router — keeps 1COMMERCE LLC inside the Oregon DFR
money-transmitter exemption by routing fractional sales through Securitize,
a registered transfer agent + broker-dealer custodian.
"""
from __future__ import annotations

from dataclasses import dataclass

from loguru import logger

from pacer.compliance.audit import record_event
from pacer.config import get_settings
from pacer.models.domain_candidate import DomainCandidate, Status
from pacer.rwa.doma_client import DomaClient, DomaToken
from pacer.utils.api_resilience import build_client, resilient_api

settings = get_settings()


@dataclass(slots=True)
class SecuritizeOffering:
    offering_id: str
    domain: str
    token_id: str
    status: str


class SecuritizeRouter:
    """Creates a Securitize offering, associates the Doma DST, enables KYC/AML."""

    def __init__(self) -> None:
        self._client = None

    async def __aenter__(self) -> "SecuritizeRouter":
        self._client = build_client(
            base_url=settings.securitize_api_url,
            headers={
                "Authorization": f"Bearer {settings.securitize_api_key.get_secret_value()}",
                "Content-Type": "application/json",
            },
        )
        return self

    async def __aexit__(self, *exc) -> None:
        if self._client:
            await self._client.aclose()

    @resilient_api(endpoint="securitize.create_offering")
    async def create_offering(self, token: DomaToken, total_supply: int) -> SecuritizeOffering:
        if not settings.rwa_fractional_sales_enabled:
            raise RuntimeError(
                "Fractional RWA sales disabled. Obtain DFR opinion letter, "
                "then set RWA_FRACTIONAL_SALES_ENABLED=true."
            )
        assert self._client is not None
        resp = await self._client.post(
            "/issuances",
            json={
                "issuerId": settings.securitize_issuer_id,
                "name": token.domain,
                "symbol": token.domain.split(".")[0].upper()[:10],
                "totalSupply": total_supply,
                "assetType": "RWA_DOMAIN",
                "linkedToken": {
                    "chain": token.chain_id,
                    "contract": "doma-bridge",
                    "tokenId": token.token_id,
                },
                "kyc": {"required": True, "program": "securitize-id"},
                "llc_entity": settings.llc_entity,
            },
        )
        resp.raise_for_status()
        data = resp.json()
        offering = SecuritizeOffering(
            offering_id=data["id"],
            domain=token.domain,
            token_id=token.token_id,
            status=data.get("status", "pending"),
        )
        logger.info("securitize_offering_created domain={} id={}", token.domain, offering.offering_id)
        return offering


async def tokenize(candidate: DomainCandidate) -> DomainCandidate:
    """Full tokenization flow — mint DST, create Securitize offering, update record."""
    if (candidate.score or 0) < settings.score_threshold_dropcatch:
        logger.debug("tokenize_skipped domain={} score={}", candidate.domain, candidate.score)
        return candidate

    # 1. Mint DST on Doma
    async with DomaClient() as doma:
        token = await doma.mint_dst(
            domain=candidate.domain,
            total_supply=1_000_000,
            reserve_price_wei="0",
        )

    await record_event(
        event_type="rwa_mint_dst",
        endpoint="doma.mint_dst",
        domain=candidate.domain,
        message=f"token_id={token.token_id}",
    )

    candidate.rwa_token_id = token.token_id
    candidate.rwa_type = "DST"

    # 2. Route through Securitize (if exemption enabled)
    if settings.rwa_fractional_sales_enabled:
        async with SecuritizeRouter() as sec:
            offering = await sec.create_offering(token, total_supply=1_000_000)
        candidate.securitize_offering_id = offering.offering_id
        await record_event(
            event_type="rwa_offering_created",
            endpoint="securitize.create_offering",
            domain=candidate.domain,
            message=f"offering_id={offering.offering_id}",
        )

    candidate.status = Status.TOKENIZED
    return candidate
