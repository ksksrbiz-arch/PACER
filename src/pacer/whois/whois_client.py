"""Async-safe wrapper around python-whois.

`whois.whois()` is a synchronous call that opens a TCP socket and blocks on
network I/O. Calling it directly from an async function stalls the event loop
for every pending coroutine — during a large batch run that means everything
else (DB writes, HTTP calls, scheduler ticks) pauses until each WHOIS returns.

`asyncio.to_thread` dispatches the blocking call to the default executor so
the event loop stays responsive.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import whois
from loguru import logger

DEFAULT_TIMEOUT_SECONDS = 15.0


class WhoisLookupError(RuntimeError):
    """Raised when WHOIS lookup fails or returns no usable record."""


@dataclass(slots=True, frozen=True)
class WhoisRecord:
    domain: str
    registrar: str | None
    creation_date: datetime | None
    expiration_date: datetime | None
    status: tuple[str, ...]
    raw: dict[str, Any]

    @property
    def is_registered(self) -> bool:
        """python-whois returns an empty-ish record when a domain is available."""
        return bool(self.registrar or self.creation_date)


def _coerce_datetime(value: Any) -> datetime | None:
    """python-whois sometimes returns a list of dates; normalize to one."""
    if isinstance(value, list):
        value = next((v for v in value if v is not None), None)
    return value if isinstance(value, datetime) else None


def _coerce_status(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, (list, tuple)):
        return tuple(str(v) for v in value if v)
    return (str(value),)


async def lookup(domain: str) -> WhoisRecord:
    """Run a WHOIS query off the event loop. Raises WhoisLookupError on failure.

    Callers can enforce their own deadline with `asyncio.timeout(...)`; a
    module-level default bounds the underlying socket call.
    """
    try:
        async with asyncio.timeout(DEFAULT_TIMEOUT_SECONDS):
            raw = await asyncio.to_thread(whois.whois, domain)
    except TimeoutError as exc:
        logger.warning("whois_timeout domain={} timeout={}", domain, DEFAULT_TIMEOUT_SECONDS)
        raise WhoisLookupError(f"whois timeout for {domain}") from exc
    except Exception as exc:
        logger.warning("whois_failed domain={} err={}", domain, exc)
        raise WhoisLookupError(str(exc)) from exc

    data: dict[str, Any] = dict(raw) if raw else {}
    return WhoisRecord(
        domain=domain,
        registrar=data.get("registrar") or None,
        creation_date=_coerce_datetime(data.get("creation_date")),
        expiration_date=_coerce_datetime(data.get("expiration_date")),
        status=_coerce_status(data.get("status")),
        raw=data,
    )
