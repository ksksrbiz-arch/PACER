"""``pacer revenue`` CLI tests with shared in-memory SQLite."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta

import pytest
from click.exceptions import BadParameter
from click.testing import CliRunner
from pacer.models.base import Base
from pacer.models.domain_candidate import DomainCandidate, PipelineSource, Status
from sqlalchemy import event, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


def _enable_sqlite_fks(dbapi_conn, _):
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA foreign_keys=ON")
    cur.close()


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def engine():
    eng = create_async_engine(
        "sqlite+aiosqlite:///file:pacer-revenue-memdb?mode=memory&cache=shared&uri=true",
        future=True,
    )
    event.listen(eng.sync_engine, "connect", _enable_sqlite_fks)

    async def _setup():
        async with eng.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)

    _run(_setup())
    yield eng
    _run(eng.dispose())


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
def seeded_candidates(engine):
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def _seed():
        async with maker() as s:
            s.add_all(
                [
                    DomainCandidate(
                        domain="edgar-fresh.com",
                        company_name="EDGAR Co",
                        source=PipelineSource.EDGAR,
                        llc_entity="1COMMERCE LLC",
                        status=Status.DISCOVERED,
                        score=63.5,
                        source_record_id="ed-1",
                    ),
                    DomainCandidate(
                        domain="uspto-scored.com",
                        company_name="USPTO Co",
                        source=PipelineSource.USPTO,
                        llc_entity="1COMMERCE LLC",
                        status=Status.SCORED,
                        score=88.0,
                        source_record_id="tm-1",
                    ),
                    DomainCandidate(
                        domain="low-old.com",
                        source=PipelineSource.SOS_DISSOLUTION,
                        llc_entity="1COMMERCE LLC",
                        status=Status.DISCOVERED,
                        score=10.0,
                        source_record_id="sos-1",
                    ),
                ]
            )
            await s.commit()

            old_time = datetime.now(UTC) - timedelta(days=10)
            await s.execute(
                update(DomainCandidate)
                .where(DomainCandidate.domain == "low-old.com")
                .values(updated_at=old_time)
            )
            await s.commit()

    _run(_seed())


@pytest.fixture
def runner():
    return CliRunner()


def _invoke(runner: CliRunner, args: list[str]):
    from pacer.main import cli

    return runner.invoke(cli, args, catch_exceptions=False)


def test_revenue_list_signals_default_since_returns_recent(
    patched_session, seeded_candidates, runner
):
    result = _invoke(runner, ["revenue", "list-signals"])
    assert result.exit_code == 0, result.output
    rows = json.loads(result.stdout)
    assert {r["domain"] for r in rows} == {"edgar-fresh.com", "uspto-scored.com"}


def test_revenue_list_signals_source_status_filter(patched_session, seeded_candidates, runner):
    result = _invoke(
        runner,
        [
            "revenue",
            "list-signals",
            "--since",
            "30d",
            "--source",
            "uspto",
            "--status",
            "scored",
        ],
    )
    assert result.exit_code == 0, result.output
    rows = json.loads(result.stdout)
    assert [r["domain"] for r in rows] == ["uspto-scored.com"]


def test_revenue_list_signals_min_score_filter(patched_session, seeded_candidates, runner):
    result = _invoke(runner, ["revenue", "list-signals", "--since", "30d", "--min-score", "70"])
    assert result.exit_code == 0, result.output
    rows = json.loads(result.stdout)
    assert [r["domain"] for r in rows] == ["uspto-scored.com"]


def test_revenue_list_signals_bad_since_fails(patched_session, seeded_candidates, runner):
    result = _invoke(runner, ["revenue", "list-signals", "--since", "recently"])
    assert result.exit_code != 0
    assert "--since must look like" in result.output


def test_revenue_list_signals_limit_applies(patched_session, seeded_candidates, runner, engine):
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def _age():
        async with maker() as s:
            now = datetime.now(UTC)
            await s.execute(
                update(DomainCandidate)
                .where(DomainCandidate.domain == "edgar-fresh.com")
                .values(updated_at=now - timedelta(minutes=2))
            )
            await s.execute(
                update(DomainCandidate)
                .where(DomainCandidate.domain == "uspto-scored.com")
                .values(updated_at=now - timedelta(minutes=1))
            )
            await s.commit()

    _run(_age())

    result = _invoke(runner, ["revenue", "list-signals", "--since", "1d", "--limit", "1"])
    assert result.exit_code == 0, result.output
    rows = json.loads(result.stdout)
    assert len(rows) == 1
    assert rows[0]["domain"] == "uspto-scored.com"


def test_parse_since_supports_common_units():
    from pacer.cli.revenue import _parse_since

    now = datetime.now(UTC)
    parsed = _parse_since("24h")
    assert abs((now - parsed) - timedelta(hours=24)) < timedelta(seconds=5)

    parsed2 = _parse_since("7d")
    assert abs((now - parsed2) - timedelta(days=7)) < timedelta(seconds=5)

    with pytest.raises(BadParameter):
        _parse_since("later")
