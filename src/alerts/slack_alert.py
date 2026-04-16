"""
Slack alert client for pipeline notifications.
"""

import httpx
from loguru import logger

from src.config import Config


async def send_slack(message: str) -> None:
    """Post a message to the configured Slack webhook."""
    if not Config.SLACK_WEBHOOK_URL:
        logger.debug("Slack webhook not configured — skipping alert")
        return
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                Config.SLACK_WEBHOOK_URL,
                json={"text": message},
            )
            resp.raise_for_status()
    except Exception as exc:
        logger.error(f"Slack alert failed: {exc}")


async def alert_pipeline_complete(candidate_count: int, qualified_count: int) -> None:
    await send_slack(
        f"✅ *PACER daily pipeline complete* | "
        f"{candidate_count} candidates scraped, "
        f"{qualified_count} qualified for drop-catch/RWA | "
        f"Entity: {Config.LLC_ENTITY}"
    )


async def alert_pipeline_error(error: str) -> None:
    await send_slack(f"🚨 *PACER pipeline ERROR* | {error} | Entity: {Config.LLC_ENTITY}")
