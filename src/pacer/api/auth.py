"""API key authentication dependency for PACER REST endpoints.

Callers must include the header::

    X-API-Key: <secret>

If ``settings.api_key`` is blank the guard is disabled — useful for
local development without key material.  In production always set a
strong random value.
"""

from __future__ import annotations

from fastapi import Header, HTTPException, status

from pacer.config import get_settings

_settings = get_settings()


async def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    """FastAPI dependency: validate ``X-API-Key`` header.

    Raises 401 when the header is missing or wrong.
    Passes through without checking when ``settings.api_key`` is blank.
    """
    secret = _settings.api_key.get_secret_value()
    if not secret:
        return  # key enforcement disabled

    if x_api_key is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="X-API-Key header required",
        )
    if x_api_key != secret:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid API key",
        )
