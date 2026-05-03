"""Shared resilience decorator — retries, circuit breaker, Retry-After, compliance log."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from functools import wraps
from typing import ParamSpec, TypeVar

import httpx
from loguru import logger
from tenacity import (
    AsyncRetrying,
    RetryError,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential_jitter,
)

from pacer.config import get_settings

P = ParamSpec("P")
R = TypeVar("R")

_settings = get_settings()


@dataclass
class CircuitBreaker:
    """Simple in-process circuit breaker. Keyed by endpoint label."""

    failure_threshold: int = 5
    reset_after_seconds: float = 60.0

    _failures: dict[str, int] = field(default_factory=dict)
    _opened_at: dict[str, float] = field(default_factory=dict)

    def is_open(self, key: str) -> bool:
        opened = self._opened_at.get(key)
        if opened is None:
            return False
        if time.monotonic() - opened > self.reset_after_seconds:
            # half-open → reset
            self._failures[key] = 0
            self._opened_at.pop(key, None)
            return False
        return True

    def record_failure(self, key: str) -> None:
        self._failures[key] = self._failures.get(key, 0) + 1
        if self._failures[key] >= self.failure_threshold:
            self._opened_at[key] = time.monotonic()
            logger.warning("circuit_open key={} failures={}", key, self._failures[key])

    def record_success(self, key: str) -> None:
        if self._failures.get(key):
            logger.info("circuit_reset key={}", key)
        self._failures[key] = 0
        self._opened_at.pop(key, None)


breaker = CircuitBreaker()


class CircuitOpenError(RuntimeError):
    """Raised when the circuit breaker is open for an endpoint."""


TRANSIENT = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.ReadTimeout,
    httpx.WriteTimeout,
    httpx.PoolTimeout,
    httpx.RemoteProtocolError,
)

NON_RETRYABLE_STATUSES = (400, 401, 403, 404)


def _should_retry(exc: BaseException) -> bool:
    """Retry on transient network errors and retryable HTTP statuses only."""
    if isinstance(exc, TRANSIENT):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code not in NON_RETRYABLE_STATUSES
    return False


def resilient_api(
    *,
    endpoint: str,
    max_attempts: int = 5,
    min_wait: float = 1.0,
    max_wait: float = 30.0,
    honor_retry_after: bool = True,
) -> Callable[[Callable[P, Awaitable[R]]], Callable[P, Awaitable[R]]]:
    """Decorator for async HTTP calls.

    - Tenacity exponential backoff + jitter on transient errors.
    - Honors Retry-After on 429/503.
    - In-process circuit breaker keyed by `endpoint`.
    - Emits structured compliance logs for every failure.
    """

    def decorator(func: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
        @wraps(func)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            if breaker.is_open(endpoint):
                raise CircuitOpenError(f"circuit open: {endpoint}")

            try:
                async for attempt in AsyncRetrying(
                    stop=stop_after_attempt(max_attempts),
                    wait=wait_exponential_jitter(initial=min_wait, max=max_wait),
                    retry=retry_if_exception(_should_retry),
                    reraise=True,
                ):
                    with attempt:
                        try:
                            result = await func(*args, **kwargs)
                        except httpx.HTTPStatusError as exc:
                            status = exc.response.status_code
                            if status in (429, 503) and honor_retry_after:
                                retry_after = exc.response.headers.get("Retry-After")
                                if retry_after:
                                    try:
                                        await asyncio.sleep(float(retry_after))
                                    except ValueError:
                                        pass
                            if status in (400, 401, 403, 404):
                                # non-retryable — fail fast
                                await _log_compliance(
                                    event="api_non_retryable",
                                    endpoint=endpoint,
                                    status=status,
                                    message=str(exc),
                                )
                                raise
                            logger.warning(
                                "api_retry endpoint={} status={} attempt={}",
                                endpoint,
                                status,
                                attempt.retry_state.attempt_number,
                            )
                            raise
                        breaker.record_success(endpoint)
                        return result
            except RetryError as exc:
                breaker.record_failure(endpoint)
                await _log_compliance(
                    event="api_retry_exhausted",
                    endpoint=endpoint,
                    message=str(exc),
                )
                raise
            except Exception as exc:
                breaker.record_failure(endpoint)
                await _log_compliance(
                    event="api_exception",
                    endpoint=endpoint,
                    message=str(exc),
                )
                raise
            # unreachable
            raise RuntimeError("unreachable")

        return wrapper

    return decorator


async def _log_compliance(
    *, event: str, endpoint: str, status: int | None = None, message: str | None = None
) -> None:
    """Defer import to avoid circular — write structured audit row."""
    try:
        from pacer.compliance.audit import record_event  # local import

        await record_event(
            event_type=event,
            endpoint=endpoint,
            http_status=status,
            message=message,
            severity="error" if event != "api_retry" else "warning",
        )
    except Exception:  # pragma: no cover — audit must never break the hot path
        logger.exception("compliance_log_failed event={} endpoint={}", event, endpoint)


def build_client(
    *,
    base_url: str,
    headers: dict[str, str] | None = None,
    timeout: float = 30.0,
) -> httpx.AsyncClient:
    """Factory: standardized httpx.AsyncClient."""
    return httpx.AsyncClient(
        base_url=base_url,
        headers={
            "User-Agent": f"pacer-bot/{_settings.llc_entity} <{_settings.sec_user_agent}>",
            **(headers or {}),
        },
        timeout=httpx.Timeout(timeout),
        follow_redirects=True,
    )
