"""
Shared API resilience utilities for the PACER platform.

Provides:
  - resilient_api_call: async decorator with exponential-backoff retry,
    Retry-After handling, and a per-endpoint circuit breaker.
  - log_compliance: structured compliance/audit logger for 1COMMERCE LLC.
"""

import asyncio
import time
from functools import wraps
from typing import Any, Callable

import httpx
from circuitbreaker import CircuitBreakerError, circuit
from loguru import logger
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)


class APIResilience:
    @staticmethod
    def resilient_api_call(max_attempts: int = 5, base_wait: int = 2) -> Callable:
        """
        Decorator for any async API call.

        Applies:
          - Circuit breaker (3 failures → 5-minute pause)
          - Exponential backoff with jitter (up to 60 s between retries)
          - Retry-After header handling for 429 responses
          - Structured error logging for 401, 5xx, and non-retryable codes
        """

        def decorator(func: Callable) -> Callable:
            @wraps(func)
            @circuit(failure_threshold=3, recovery_timeout=300, name=func.__name__)
            @retry(
                stop=stop_after_attempt(max_attempts),
                wait=wait_exponential_jitter(initial=base_wait, max=60),
                retry=retry_if_exception_type(
                    (httpx.TimeoutException, httpx.ConnectError, httpx.HTTPStatusError)
                ),
                before_sleep=before_sleep_log(logger, "WARNING"),  # type: ignore[arg-type]
                reraise=True,
            )
            async def wrapper(*args: Any, **kwargs: Any) -> Any:
                start = time.monotonic()
                try:
                    result = await func(*args, **kwargs)
                    duration = time.monotonic() - start
                    logger.info(f"✅ {func.__name__} succeeded in {duration:.2f}s")
                    return result
                except httpx.HTTPStatusError as exc:
                    status = exc.response.status_code
                    if status == 429:
                        retry_after = int(exc.response.headers.get("Retry-After", 60))
                        logger.warning(
                            f"⚠️  Rate limit on {func.__name__} — sleeping {retry_after}s"
                        )
                        await asyncio.sleep(retry_after)
                    elif status == 401:
                        logger.error(
                            f"🔑 Auth failure in {func.__name__} — check credentials / NextGenCSO key"
                        )
                    elif status >= 500:
                        logger.warning(f"🔴 Server error {status} in {func.__name__} — will retry")
                    else:
                        logger.error(
                            f"❌ Non-retryable {status} in {func.__name__}: "
                            f"{exc.response.text[:200]}"
                        )
                    raise
                except CircuitBreakerError:
                    logger.error(f"🚫 Circuit breaker open for {func.__name__} — falling back")
                    raise

            return wrapper

        return decorator

    @staticmethod
    async def log_compliance(event: str, details: dict) -> None:
        """
        Audit logger for 1COMMERCE LLC.

        Writes to the application log with a structured compliance tag.
        In production, also persists to the compliance_logs Postgres table.
        """
        from src.config import Config

        logger.info(
            f"COMPLIANCE LOG | entity={Config.LLC_ENTITY} | event={event} | details={details}"
        )
        # Database persistence is handled by ComplianceLogger in src/compliance/
