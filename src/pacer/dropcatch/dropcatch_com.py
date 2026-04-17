"""DropCatch.com backorder fallback."""
from __future__ import annotations

from loguru import logger

from pacer.config import get_settings
from pacer.utils.api_resilience import build_client, resilient_api

settings = get_settings()


@resilient_api(endpoint="dropcatch.backorder")
async def place_backorder(domain: str) -> dict:
    user = settings.dropcatch_user
    key = settings.dropcatch_key.get_secret_value()
    if not user or not key:
        return {"ok": False, "reason": "no_credentials"}
    async with build_client(base_url="https://api.dropcatch.com") as c:
        resp = await c.post(
            "/v1/backorders",
            auth=(user, key),
            json={"domain": domain},
        )
        resp.raise_for_status()
        data = resp.json()
    logger.info("dropcatch_backorder domain={} id={}", domain, data.get("backorderId"))
    return {"ok": True, "raw": data}
