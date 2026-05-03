"""Tests for the shared resilience decorator + circuit breaker."""

from __future__ import annotations

import time

import httpx
import pytest
import respx
from pacer.utils.api_resilience import (
    CircuitBreaker,
    CircuitOpenError,
    breaker,
    build_client,
    resilient_api,
)


# ─────────────────────── CircuitBreaker ────────────────────────
def test_circuit_breaker_opens_after_threshold():
    cb = CircuitBreaker(failure_threshold=3, reset_after_seconds=30.0)
    for _ in range(3):
        cb.record_failure("endpoint.x")
    assert cb.is_open("endpoint.x")


def test_circuit_breaker_success_resets_failures():
    cb = CircuitBreaker(failure_threshold=3, reset_after_seconds=30.0)
    cb.record_failure("endpoint.y")
    cb.record_failure("endpoint.y")
    cb.record_success("endpoint.y")
    # One more failure should NOT re-open — counter was reset
    cb.record_failure("endpoint.y")
    assert not cb.is_open("endpoint.y")


def test_circuit_breaker_half_open_after_reset_window(monkeypatch):
    cb = CircuitBreaker(failure_threshold=2, reset_after_seconds=0.01)
    cb.record_failure("endpoint.z")
    cb.record_failure("endpoint.z")
    assert cb.is_open("endpoint.z")
    time.sleep(0.02)
    # After the reset window elapses, it should auto-close on next check
    assert not cb.is_open("endpoint.z")


# ─────────────────────── resilient_api ─────────────────────────
@pytest.mark.asyncio
async def test_resilient_api_retries_on_5xx_then_succeeds():
    route = respx.mock(base_url="https://api.example.com")
    route.get("/v1/ping").mock(
        side_effect=[
            httpx.Response(503, headers={"Retry-After": "0"}),
            httpx.Response(200, json={"ok": True}),
        ]
    )

    @resilient_api(endpoint="test.ping", max_attempts=3, min_wait=0.0, max_wait=0.01)
    async def call() -> dict:
        async with build_client(base_url="https://api.example.com") as c:
            r = await c.get("/v1/ping")
            r.raise_for_status()
            return r.json()

    with route:
        result = await call()
    assert result == {"ok": True}


@pytest.mark.asyncio
async def test_resilient_api_fails_fast_on_401():
    route = respx.mock(base_url="https://api.example.com")
    route.get("/v1/secret").mock(return_value=httpx.Response(401, json={"err": "unauth"}))

    @resilient_api(endpoint="test.secret", max_attempts=5, min_wait=0.0, max_wait=0.01)
    async def call() -> dict:
        async with build_client(base_url="https://api.example.com") as c:
            r = await c.get("/v1/secret")
            r.raise_for_status()
            return r.json()

    with route:
        with pytest.raises(httpx.HTTPStatusError):
            await call()
        # Should not have retried — respx mock counts only one call.
        # Must be asserted inside the `with route:` block because respx
        # resets its routers on exit.
        assert route.routes[0].call_count == 1


@pytest.mark.asyncio
async def test_resilient_api_raises_circuit_open_when_breaker_is_tripped():
    # Pre-trip the module-level breaker for this endpoint
    for _ in range(10):
        breaker.record_failure("test.tripped")

    @resilient_api(endpoint="test.tripped")
    async def call() -> str:  # pragma: no cover - should not be invoked
        return "nope"

    with pytest.raises(CircuitOpenError):
        await call()
