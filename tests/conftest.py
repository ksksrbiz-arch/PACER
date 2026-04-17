"""Pytest fixtures — env isolation, no real DB or network."""
from __future__ import annotations

import os

import pytest

# Force test env BEFORE importing pacer modules
os.environ.setdefault("ENVIRONMENT", "ci")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://pacer:pacer@localhost:5432/pacer_test")
os.environ.setdefault("SYNC_DATABASE_URL", "postgresql://pacer:pacer@localhost:5432/pacer_test")
os.environ.setdefault("SEC_USER_AGENT", "1COMMERCE LLC ci-tests skdev@1commercesolutions.com")


@pytest.fixture(autouse=True)
def _reset_circuit_breaker():
    """Each test starts with a clean breaker so no state leaks between cases."""
    from pacer.utils.api_resilience import breaker

    breaker._failures.clear()
    breaker._opened_at.clear()
    yield
    breaker._failures.clear()
    breaker._opened_at.clear()
