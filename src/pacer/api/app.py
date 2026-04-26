"""PACER Tier-1 REST API — distressed-domain signal feed.

Exposes the same query logic as ``pacer revenue list-signals`` over HTTP
so B2B data-licensing consumers can integrate without the CLI.

Endpoints
---------
GET  /health           — unauthenticated liveness probe
GET  /v1/signals       — authenticated, filterable signal feed

Authentication
--------------
Pass a pre-shared secret in the ``X-API-Key`` header.  Key enforcement
is disabled when ``settings.api_key`` is blank (development mode).

Usage
-----
    # Start the server (blocking):
    poetry run pacer api serve

    # Consume:
    curl -H "X-API-Key: <secret>" \
         "http://localhost:8000/v1/signals?since=24h&min_score=60"
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Annotated

import click
from fastapi import Depends, FastAPI, Query
from fastapi.responses import JSONResponse
from loguru import logger

from pacer.api.auth import require_api_key
from pacer.cli.revenue import _list_signals, _parse_since
from pacer.compliance.audit import record_event
from pacer.config import get_settings
from pacer.models.domain_candidate import PipelineSource, Status

_settings = get_settings()

# ─── Lifespan ────────────────────────────────────────────────────────────────


@asynccontextmanager
async def _lifespan(app: FastAPI):  # noqa: ARG001
    logger.info(
        "pacer_api_startup env={} host={} port={}",
        _settings.environment,
        _settings.api_host,
        _settings.api_port,
    )
    yield
    logger.info("pacer_api_shutdown")


# ─── App ─────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="PACER Signal Feed API",
    description=(
        "Tier-1 data-licensing endpoint — distressed-domain signals from "
        "1COMMERCE LLC discovery pipelines."
    ),
    version="1.0.0",
    lifespan=_lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# ─── Health ──────────────────────────────────────────────────────────────────

_VALID_SOURCES = [s.value for s in PipelineSource]
_VALID_STATUSES = [s.value for s in Status]


@app.get("/health", tags=["ops"])
async def health() -> dict:
    """Unauthenticated liveness probe."""
    return {"status": "ok", "llc_entity": _settings.llc_entity}


# ─── v1/signals ──────────────────────────────────────────────────────────────


@app.get(
    "/v1/signals",
    tags=["signals"],
    dependencies=[Depends(require_api_key)],
)
async def list_signals(
    since: Annotated[
        str,
        Query(description="Lookback window, e.g. '1h', '24h', '7d'. Default 24h."),
    ] = "24h",
    source: Annotated[
        str | None,
        Query(description=f"Pipeline source filter. One of: {', '.join(_VALID_SOURCES)}."),
    ] = None,
    status: Annotated[
        str | None,
        Query(description=f"Lifecycle status filter. One of: {', '.join(_VALID_STATUSES)}."),
    ] = None,
    min_score: Annotated[
        float | None,
        Query(ge=0.0, le=100.0, description="Minimum composite score (0–100)."),
    ] = None,
    limit: Annotated[
        int,
        Query(ge=1, le=1000, description="Max rows to return (default 500)."),
    ] = 500,
) -> JSONResponse:
    """Retrieve recent distressed-domain signals.

    All query parameters are optional. Results are ordered newest-first by
    ``updated_at``.  Each record includes domain identity, pipeline source,
    lifecycle status, SEO scoring fields, and compliance tags.
    """
    # Validate enum inputs early so callers get a clear 422 rather than a
    # server-side ValueError from the SQLAlchemy query.
    if source is not None and source not in _VALID_SOURCES:
        return JSONResponse(
            status_code=422,
            content={"detail": f"source must be one of: {_VALID_SOURCES}"},
        )
    if status is not None and status not in _VALID_STATUSES:
        return JSONResponse(
            status_code=422,
            content={"detail": f"status must be one of: {_VALID_STATUSES}"},
        )

    # Validate lookback window — surface a 422 with a human-readable message.
    try:
        _parse_since(since)
    except click.BadParameter:
        return JSONResponse(
            status_code=422,
            content={"detail": f"'since' must be like '1h', '30m', '7d'. Got: {since!r}"},
        )

    rows = await _list_signals(
        since=since, source=source, status=status, min_score=min_score, limit=limit
    )

    await record_event(
        event_type="api_signals_query",
        endpoint="GET /v1/signals",
        message=f"since={since} source={source} status={status} min_score={min_score} limit={limit} rows={len(rows)}",
    )

    return JSONResponse(
        content={"count": len(rows), "results": rows},
        status_code=200,
    )


# ─── Convenience date serialiser ──────────────────────────────────────────────
# `date` objects in `pending_delete_date` come back as Python `date` instances
# which are not JSON-serialisable by the stdlib encoder.  The signal dict
# from `_list_signals` already calls `.isoformat()` on them, so no custom
# encoder is required here; the field is returned as a string or None.

__all__ = ["app"]
