"""``GET /v1/signals`` API tests — in-memory SQLite backend.

Uses ``httpx.AsyncClient`` with the ASGI transport to drive the FastAPI
app without starting a real server.  The ``session_scope`` used by
``_list_signals`` is monkey-patched to the same in-memory engine so the
API queries the test database.  Compliance ``record_event`` is stubbed
to a no-op so tests don't need a real DB for audit writes.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from pacer.models.base import Base
from pacer.models.domain_candidate import DomainCandidate, PipelineSource, Status
from sqlalchemy import event, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


# ─── DB helpers ──────────────────────────────────────────────────────────────


def _enable_sqlite_fks(dbapi_conn, _):
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA foreign_keys=ON")
    cur.close()


@pytest_asyncio.fixture
async def engine():
    eng = create_async_engine(
        "sqlite+aiosqlite:///file:pacer-api-test?mode=memory&cache=shared&uri=true",
        future=True,
    )
    event.listen(eng.sync_engine, "connect", _enable_sqlite_fks)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def seeded(engine):
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        s.add_all(
            [
                DomainCandidate(
                    domain="edgar-api.com",
                    company_name="EDGAR API Co",
                    source=PipelineSource.EDGAR,
                    llc_entity="1COMMERCE LLC",
                    status=Status.DISCOVERED,
                    score=65.0,
                ),
                DomainCandidate(
                    domain="uspto-api.com",
                    company_name="USPTO API Co",
                    source=PipelineSource.USPTO,
                    llc_entity="1COMMERCE LLC",
                    status=Status.SCORED,
                    score=90.0,
                ),
                DomainCandidate(
                    domain="old-api.com",
                    source=PipelineSource.SOS_DISSOLUTION,
                    llc_entity="1COMMERCE LLC",
                    status=Status.DISCOVERED,
                    score=20.0,
                ),
            ]
        )
        await s.commit()

        old_time = datetime.now(UTC) - timedelta(days=10)
        await s.execute(
            update(DomainCandidate)
            .where(DomainCandidate.domain == "old-api.com")
            .values(updated_at=old_time)
        )
        await s.commit()


@pytest.fixture
def patched_session(monkeypatch, engine):
    from contextlib import asynccontextmanager

    maker = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def _scope():
        async with maker() as s:
            yield s

    monkeypatch.setattr("pacer.cli.revenue.session_scope", _scope)
    return _scope


@pytest.fixture
def stub_audit(monkeypatch):
    """No-op audit so tests don't need a real DB for compliance writes."""

    async def _noop(**kwargs):
        pass

    monkeypatch.setattr("pacer.api.app.record_event", _noop)


# ─── Client fixture ──────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def client(patched_session, stub_audit):
    from pacer.api.app import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


@pytest_asyncio.fixture
async def authed_client(patched_session, stub_audit, monkeypatch):
    """Client whose X-API-Key matches the enforced secret."""
    from pacer.api import auth as auth_mod
    from pacer.config import Settings

    monkeypatch.setattr(auth_mod, "_settings", Settings(api_key="test-secret"))  # type: ignore[call-arg]

    from pacer.api.app import app

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-API-Key": "test-secret"},
    ) as ac:
        yield ac


@pytest_asyncio.fixture
async def wrong_key_client(patched_session, stub_audit, monkeypatch):
    """Client whose X-API-Key does NOT match the enforced secret."""
    from pacer.api import auth as auth_mod
    from pacer.config import Settings

    monkeypatch.setattr(auth_mod, "_settings", Settings(api_key="test-secret"))  # type: ignore[call-arg]

    from pacer.api.app import app

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-API-Key": "wrong-key"},
    ) as ac:
        yield ac


# ─── Health ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_health_returns_200(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert "llc_entity" in data


# ─── Authentication ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_signals_no_key_enforcement_when_blank(client, seeded):
    """When api_key is blank the endpoint is open."""
    resp = await client.get("/v1/signals")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_signals_requires_valid_key_when_set(authed_client, wrong_key_client, seeded):
    resp_ok = await authed_client.get("/v1/signals")
    assert resp_ok.status_code == 200

    resp_bad = await wrong_key_client.get("/v1/signals")
    assert resp_bad.status_code == 403


@pytest.mark.asyncio
async def test_signals_missing_key_returns_401(patched_session, stub_audit, monkeypatch, seeded):
    from pacer.api import auth as auth_mod
    from pacer.config import Settings

    monkeypatch.setattr(auth_mod, "_settings", Settings(api_key="test-secret"))  # type: ignore[call-arg]

    from pacer.api.app import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/v1/signals")
    assert resp.status_code == 401


# ─── Default behaviour ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_signals_default_returns_recent_rows(client, seeded):
    resp = await client.get("/v1/signals")
    assert resp.status_code == 200
    data = resp.json()
    assert "count" in data
    assert "results" in data
    domains = {r["domain"] for r in data["results"]}
    assert "edgar-api.com" in domains
    assert "uspto-api.com" in domains
    assert "old-api.com" not in domains


# ─── Filters ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_signals_source_filter(client, seeded):
    resp = await client.get("/v1/signals", params={"since": "30d", "source": "uspto"})
    assert resp.status_code == 200
    domains = {r["domain"] for r in resp.json()["results"]}
    assert domains == {"uspto-api.com"}


@pytest.mark.asyncio
async def test_signals_status_filter(client, seeded):
    resp = await client.get("/v1/signals", params={"since": "30d", "status": "scored"})
    assert resp.status_code == 200
    domains = {r["domain"] for r in resp.json()["results"]}
    assert domains == {"uspto-api.com"}


@pytest.mark.asyncio
async def test_signals_min_score_filter(client, seeded):
    resp = await client.get("/v1/signals", params={"since": "30d", "min_score": 80})
    assert resp.status_code == 200
    domains = {r["domain"] for r in resp.json()["results"]}
    assert domains == {"uspto-api.com"}


@pytest.mark.asyncio
async def test_signals_limit_param(client, seeded):
    resp = await client.get("/v1/signals", params={"since": "30d", "limit": 1})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["results"]) == 1
    assert data["count"] == 1


# ─── Validation ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_signals_bad_since_returns_422(client, seeded):
    resp = await client.get("/v1/signals", params={"since": "whenever"})
    assert resp.status_code == 422
    assert "since" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_signals_bad_source_returns_422(client, seeded):
    resp = await client.get("/v1/signals", params={"source": "bogus_source"})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_signals_bad_status_returns_422(client, seeded):
    resp = await client.get("/v1/signals", params={"status": "nonexistent"})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_signals_limit_above_max_returns_422(client):
    resp = await client.get("/v1/signals", params={"limit": 1001})
    assert resp.status_code == 422


# ─── Response shape ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_signals_response_fields(client, seeded):
    resp = await client.get("/v1/signals", params={"since": "30d", "source": "edgar"})
    assert resp.status_code == 200
    row = resp.json()["results"][0]
    expected_keys = {
        "id",
        "domain",
        "company_name",
        "source",
        "status",
        "score",
        "domain_rating",
        "backlinks",
        "referring_domains",
        "topical_relevance",
        "spam_score",
        "pending_delete_date",
        "source_record_id",
        "updated_at",
        "created_at",
        "llc_entity",
    }
    assert expected_keys <= set(row.keys())
    assert row["domain"] == "edgar-api.com"
    assert row["source"] == "edgar"
    assert row["llc_entity"] == "1COMMERCE LLC"
