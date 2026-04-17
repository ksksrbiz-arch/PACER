"""NameJet backorder fallback."""
from __future__ import annotations

from loguru import logger

from pacer.config import get_settings
from pacer.utils.api_resilience import build_client, resilient_api

settings = get_settings()


@resilient_api(endpoint="namejet.backorder")
async def place_backorder(domain: str) -> dict:
    user = settings.namejet_user
    key = settings.namejet_key.get_secret_value()
    if not user or not key:
        return {"ok": False, "reason": "no_credentials"}
    async with build_client(base_url="https://api.namejet.com") as c:
        resp = await c.post(
            "/api/v2/backorders",
            auth=(user, key),
            json={"domain": domain},
        )
        resp.raise_for_status()
        data = resp.json()
    logger.info("namejet_backorder domain={} id={}", domain, data.get("id"))
    return {"ok": True, "raw": data}
