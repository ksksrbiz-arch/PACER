"""GoDaddy backorder fallback."""

from __future__ import annotations

from loguru import logger

from pacer.config import get_settings
from pacer.utils.api_resilience import build_client, resilient_api

settings = get_settings()


@resilient_api(endpoint="godaddy.backorder")
async def place_backorder(domain: str) -> dict:
    key = settings.godaddy_api_key.get_secret_value()
    secret = settings.godaddy_api_secret.get_secret_value()
    if not key or not secret:
        return {"ok": False, "reason": "no_credentials"}
    async with build_client(
        base_url="https://api.godaddy.com",
        headers={"Authorization": f"sso-key {key}:{secret}"},
    ) as c:
        resp = await c.post("/v1/domains/backorders", json={"domain": domain})
        resp.raise_for_status()
        data = resp.json()
    logger.info("godaddy_backorder domain={} id={}", domain, data.get("backorderId"))
    return {"ok": True, "raw": data}
