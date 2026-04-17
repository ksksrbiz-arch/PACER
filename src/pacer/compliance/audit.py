"""Audit logger — every event tagged with 1COMMERCE LLC."""
from __future__ import annotations

from typing import Any

from loguru import logger

from pacer.config import get_settings
from pacer.db import session_scope
from pacer.models.compliance_log import ComplianceLog, EventSeverity

settings = get_settings()


async def record_event(
    *,
    event_type: str,
    severity: str = "info",
    endpoint: str | None = None,
    http_status: int | None = None,
    domain: str | None = None,
    message: str | None = None,
    payload: dict[str, Any] | None = None,
) -> None:
    """Persist a compliance event. Always tagged with LLC entity."""
    sev = EventSeverity(severity) if severity in EventSeverity._value2member_map_ else EventSeverity.INFO
    merged_payload = {**(payload or {}), **settings.compliance_tags}

    logger.bind(**merged_payload).log(
        sev.value.upper() if sev.value != "info" else "INFO",
        "audit event={} endpoint={} status={} domain={}: {}",
        event_type,
        endpoint,
        http_status,
        domain,
        message,
    )

    try:
        async with session_scope() as sess:
            sess.add(
                ComplianceLog(
                    llc_entity=settings.llc_entity,
                    event_type=event_type,
                    severity=sev,
                    endpoint=endpoint,
                    http_status=http_status,
                    domain=domain,
                    message=message,
                    payload=merged_payload,
                )
            )
    except Exception:  # pragma: no cover
        logger.exception("compliance_persist_failed event={}", event_type)
