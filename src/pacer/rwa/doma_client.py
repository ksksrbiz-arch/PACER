"""Doma Protocol client — DOT (whole-domain token) and DST (fractional)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from loguru import logger

from pacer.config import get_settings
from pacer.utils.api_resilience import build_client, resilient_api

settings = get_settings()


@dataclass(slots=True)
class DomaToken:
    token_id: str
    domain: str
    token_type: Literal["DOT", "DST"]
    chain_id: int
    tx_hash: str


class DomaClient:
    """Thin wrapper around Doma registrar-bridge REST API."""

    def __init__(self) -> None:
        self._client = None

    async def __aenter__(self) -> DomaClient:
        self._client = build_client(
            base_url=settings.doma_api_url,
            headers={
                "Authorization": f"Bearer {settings.doma_api_key.get_secret_value()}",
                "Content-Type": "application/json",
            },
        )
        return self

    async def __aexit__(self, *exc) -> None:
        if self._client:
            await self._client.aclose()

    @resilient_api(endpoint="doma.mint_dot")
    async def mint_dot(self, domain: str, owner_address: str) -> DomaToken:
        """Mint a whole-domain DOT — full control, no fractions."""
        assert self._client is not None
        resp = await self._client.post(
            "/v1/tokens/dot/mint",
            json={
                "domain": domain,
                "owner": owner_address,
                "chainId": settings.doma_chain_id,
                "llc_entity": settings.llc_entity,
            },
        )
        resp.raise_for_status()
        data = resp.json()
        logger.info("doma_dot_minted domain={} token_id={}", domain, data["tokenId"])
        return DomaToken(
            token_id=data["tokenId"],
            domain=domain,
            token_type="DOT",
            chain_id=settings.doma_chain_id,
            tx_hash=data.get("txHash", ""),
        )

    @resilient_api(endpoint="doma.mint_dst")
    async def mint_dst(self, domain: str, total_supply: int, reserve_price_wei: str) -> DomaToken:
        """Mint fractional DST shares — MUST flow through Securitize for DFR exemption."""
        assert self._client is not None
        resp = await self._client.post(
            "/v1/tokens/dst/mint",
            json={
                "domain": domain,
                "totalSupply": total_supply,
                "reservePriceWei": reserve_price_wei,
                "chainId": settings.doma_chain_id,
                "llc_entity": settings.llc_entity,
            },
        )
        resp.raise_for_status()
        data = resp.json()
        logger.info(
            "doma_dst_minted domain={} token_id={} supply={}",
            domain,
            data["tokenId"],
            total_supply,
        )
        return DomaToken(
            token_id=data["tokenId"],
            domain=domain,
            token_type="DST",
            chain_id=settings.doma_chain_id,
            tx_hash=data.get("txHash", ""),
        )
