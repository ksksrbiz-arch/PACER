"""Slack webhook client for pipeline notifications.

All sends are best-effort: a failed webhook never raises into the caller.
Webhook URL comes from :class:`pacer.config.Settings.slack_webhook_url`;
if it's empty, calls become no-ops (useful in dev / CI).
"""
from __future__ import annotations

import httpx
from loguru import logger

from pacer.config import get_settings


async def send_slack(message: str) -> None:
    """Post ``message`` to the configured Slack webhook.

    Silently no-ops when ``slack_webhook_url`` is unset. Errors are logged,
    not raised — alerting failures must never break the pipeline.
    """
    settings = get_settings()
    webhook = settings.slack_webhook_url.get_secret_value()
    if not webhook:
        logger.debug("slack.webhook_unset — skipping alert")
        return
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(webhook, json={"text": message})
            resp.raise_for_status()
    except Exception as exc:  # pragma: no cover - best-effort path
        logger.error("slack.post_failed error={}", exc)


async def alert_pipeline_complete(
    candidate_count: int,
    qualified_count: int,
) -> None:
    """Daily pipeline summary."""
    settings = get_settings()
    await send_slack(
        f"✅ *PACER daily pipeline complete* | "
        f"{candidate_count} candidates scraped, "
        f"{qualified_count} qualified for drop-catch/RWA | "
        f"Entity: {settings.llc_entity}"
    )


async def alert_pipeline_error(error: str) -> None:
    """Pipeline failure alert — paged to ops."""
    settings = get_settings()
    await send_slack(
        f"🚨 *PACER pipeline ERROR* | {error} | Entity: {settings.llc_entity}"
    )
