"""Dynadot drop-catch backorder — primary registrar."""
from __future__ import annotations

from loguru import logger

from pacer.config import get_settings
from pacer.utils.api_resilience import build_client, resilient_api

settings = get_settings()


@resilient_api(endpoint="dynadot.backorder")
async def place_backorder(domain: str) -> dict:
    key = settings.dynadot_api_key.get_secret_value()
    if not key:
        return {"ok": False, "reason": "no_api_key"}
    async with build_client(base_url="https://api.dynadot.com") as c:
        resp = await c.get(
            "/api3.json",
            params={"key": key, "command": "backorder_request", "domain": domain},
        )
        resp.raise_for_status()
        data = resp.json()
    ok = data.get("BackorderResponse", {}).get("ResponseCode") == "0"
    logger.info("dynadot_backorder domain={} ok={}", domain, ok)
    return {"ok": ok, "raw": data}
