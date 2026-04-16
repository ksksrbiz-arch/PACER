"""
Tests for src/utils/api_resilience.py
"""

from unittest.mock import patch

import httpx
import pytest

from src.utils.api_resilience import APIResilience


@pytest.mark.asyncio
async def test_resilient_api_call_succeeds_on_first_attempt():
    """Decorated function succeeds and returns result normally."""

    @APIResilience.resilient_api_call(max_attempts=3)
    async def my_func():
        return {"data": 42}

    result = await my_func()
    assert result == {"data": 42}


@pytest.mark.asyncio
async def test_log_compliance_logs_without_error():
    """log_compliance should not raise even without a DB connection."""
    # Should not raise
    await APIResilience.log_compliance("test_event", {"key": "value"})


@pytest.mark.asyncio
async def test_resilient_api_call_respects_retry_after_on_429():
    """On 429, the decorator should sleep for Retry-After seconds before retrying."""
    call_count = 0
    sleep_calls = []

    async def fake_sleep(seconds):
        sleep_calls.append(seconds)

    @APIResilience.resilient_api_call(max_attempts=2, base_wait=0)
    async def my_func():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            response = httpx.Response(429, headers={"Retry-After": "5"}, text="rate limited")
            raise httpx.HTTPStatusError("rate limited", request=None, response=response)
        return "ok"

    with patch("src.utils.api_resilience.asyncio.sleep", side_effect=fake_sleep):
        result = await my_func()

    assert result == "ok"
    assert 5 in sleep_calls


@pytest.mark.asyncio
async def test_resilient_api_call_reraises_after_max_attempts():
    """After max_attempts are exhausted, the exception should propagate."""

    @APIResilience.resilient_api_call(max_attempts=2, base_wait=0)
    async def always_fails():
        raise httpx.ConnectError("Connection refused")

    with pytest.raises(httpx.ConnectError):
        await always_fails()
