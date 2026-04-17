"""``pacer monetization`` CLI — end-to-end with in-memory SQLite.

CliRunner runs the click command synchronously (the command itself calls
asyncio.run internally), so these tests are sync. DB setup/inspection
is done by running async coroutines with asyncio.run in a helper.
"""
from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta

import pytest
from click.testing import CliRunner
from sqlalchemy import event, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from pacer.models.base import Base
from pacer.models.domain_candidate import (
    DomainCandidate,
    PipelineSource,
    Status,
)


def _enable_sqlite_fks(dbapi_conn, _):
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA foreign_keys=ON")
    cur.close()


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def engine():
    """Async engine backed by a *shared* in-memory SQLite so the CLI,
    the patched session_scope, and the test all hit the same DB."""
    eng = create_async_engine(
        "sqlite+aiosqlite:///file:pacer-memdb?mode=memory&cache=shared&uri=true",
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
    """Monkeypatch the module-level session_scope in pacer.cli.monetization
    to yield async sessions bound to the test engine."""
    from contextlib import asynccontextmanager

    maker = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def _scope():
        async with maker() as s:
            yield s

    monkeypatch.setattr("pacer.cli.monetization.session_scope", _scope)
    return _scope


@pytest.fixture
def stub_externals(monkeypatch):
    """No-op Cloudflare + Afternic so route_and_list stays offline."""
    from pacer.monetization import afternic as afternic_mod
    from pacer.monetization import cloudflare as cf_mod

    async def fake_cf(domain, target, zone_id=None):
        return cf_mod.RedirectResult(
            provider="cloudflare",
            domain=domain,
            zone_id="z",
            target_url=target,
            status="dry_run",
        )

    async def fake_auction(domain, bin_price_cents, **_kwargs):
        return [
            afternic_mod.ListingResult(
                provider="afternic",
                domain=domain,
                listing_id="stub",
                listing_url=f"https://www.afternic.com/domain/{domain}",
                bin_price_cents=bin_price_cents,
                status="dry_run",
            )
        ]

    async def fake_lto(domain, bin_price_cents, monthly_cents, **_kwargs):
        return afternic_mod.ListingResult(
            provider="dan",
            domain=domain,
            listing_id="stub-lto",
            listing_url=f"https://dan.com/buy-domain/{domain}",
            bin_price_cents=bin_price_cents,
            status="dry_run",
        )

    monkeypatch.setattr(cf_mod, "configure_cloudflare_redirect", fake_cf)
    monkeypatch.setattr(afternic_mod, "post_auction_listing", fake_auction)
    monkeypatch.setattr(afternic_mod, "post_lto_listing", fake_lto)


@pytest.fixture
def runner():
    return CliRunner()


def _invoke(runner: CliRunner, args: list[str]):
    from pacer.main import cli

    return runner.invoke(cli, args, catch_exceptions=False)


# ─── tier profile sanity ────────────────────────────────────────────


def test_tier_profiles_cover_all_strategies():
    from pacer.cli.monetization import TIER_PROFILES

    assert set(TIER_PROFILES) == {
        "auction_bin",
        "lease_to_own",
        "301_redirect",
        "parking",
        "aftermarket",
    }


# ─── route-one ──────────────────────────────────────────────────────


def test_route_one_auction_tier_persists_and_lists(
    patched_session, stub_externals, runner, engine
):
    result = _invoke(
        runner,
        ["monetization", "route-one", "--domain", "canary-auction.com", "--tier", "auction_bin"],
    )
    assert result.exit_code == 0, result.output

    payload = json.loads(result.stdout)
    assert payload["domain"] == "canary-auction.com"
    assert payload["requested_tier"] == "auction_bin"
    assert payload["resolved_strategy"] == "auction_bin"
    assert payload["auction_listing_url"].startswith("https://www.afternic.com/domain/")
    assert payload["persisted"] is True

    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def _fetch():
        async with maker() as s:
            return (
                await s.execute(
                    select(DomainCandidate).where(
                        DomainCandidate.domain == "canary-auction.com"
                    )
                )
            ).scalar_one()

    row = _run(_fetch())
    assert row.monetization_strategy == "auction_bin"
    assert row.status == Status.MONETIZED


def test_route_one_301_tier_resolves_to_redirect(
    patched_session, stub_externals, runner
):
    result = _invoke(
        runner,
        ["monetization", "route-one", "--domain", "canary-301.com", "--tier", "301_redirect"],
    )
    assert result.exit_code == 0, result.output

    payload = json.loads(result.stdout)
    assert payload["resolved_strategy"] == "301_redirect"
    assert payload["redirect_target"] is not None
    # auction URL should NOT be set for redirect tier
    assert payload["auction_listing_url"] is None


def test_route_one_no_persist_flag(patched_session, stub_externals, runner, engine):
    result = _invoke(
        runner,
        [
            "monetization", "route-one",
            "--domain", "canary-nopersist.com",
            "--tier", "parking",
            "--no-persist",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["persisted"] is False

    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def _fetch():
        async with maker() as s:
            return list(
                (
                    await s.execute(
                        select(DomainCandidate).where(
                            DomainCandidate.domain == "canary-nopersist.com"
                        )
                    )
                ).scalars()
            )

    assert _run(_fetch()) == []


def test_route_one_rejects_unknown_tier(runner):
    result = _invoke(
        runner,
        ["monetization", "route-one", "--domain", "x.com", "--tier", "bogus"],
    )
    assert result.exit_code != 0
    assert "Invalid value" in result.output or "Usage" in result.output


# ─── list-recent ─────────────────────────────────────────────────────


@pytest.fixture
def seeded_monetized(engine):
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def _seed():
        async with maker() as s:
            s.add_all(
                [
                    DomainCandidate(
                        domain="fresh-auction.com",
                        source=PipelineSource.SOS_DISSOLUTION,
                        llc_entity="1COMMERCE LLC",
                        status=Status.MONETIZED,
                        monetization_strategy="auction_bin",
                        auction_listing_url="https://www.afternic.com/domain/fresh-auction.com",
                        score=95.0,
                    ),
                    DomainCandidate(
                        domain="fresh-301.com",
                        source=PipelineSource.SOS_DISSOLUTION,
                        llc_entity="1COMMERCE LLC",
                        status=Status.MONETIZED,
                        monetization_strategy="301_redirect",
                        redirect_target="https://1commercesolutions.com/resources/fresh",
                        score=72.0,
                    ),
                    DomainCandidate(
                        domain="discovered.com",
                        source=PipelineSource.SOS_DISSOLUTION,
                        llc_entity="1COMMERCE LLC",
                        status=Status.DISCOVERED,
                        score=10.0,
                    ),
                ]
            )
            await s.commit()

    _run(_seed())


def test_list_recent_returns_monetized_only(
    patched_session, seeded_monetized, runner
):
    result = _invoke(runner, ["monetization", "list-recent", "--since", "1 hour ago"])
    assert result.exit_code == 0, result.output

    rows = json.loads(result.stdout)
    domains = {r["domain"] for r in rows}
    assert "fresh-auction.com" in domains
    assert "fresh-301.com" in domains
    assert "discovered.com" not in domains


def test_list_recent_filters_by_tier(patched_session, seeded_monetized, runner):
    result = _invoke(
        runner,
        ["monetization", "list-recent", "--since", "1h", "--tier", "auction_bin"],
    )
    assert result.exit_code == 0, result.output
    rows = json.loads(result.stdout)
    assert [r["domain"] for r in rows] == ["fresh-auction.com"]


def test_list_recent_respects_since_window(
    patched_session, seeded_monetized, runner, engine
):
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def _age():
        async with maker() as s:
            old = datetime.now(UTC) - timedelta(days=2)
            await s.execute(
                update(DomainCandidate)
                .where(DomainCandidate.domain == "fresh-auction.com")
                .values(updated_at=old)
            )
            await s.commit()

    _run(_age())

    # 1h window should exclude it
    result = _invoke(runner, ["monetization", "list-recent", "--since", "1h"])
    assert result.exit_code == 0, result.output
    rows = json.loads(result.stdout)
    assert "fresh-auction.com" not in {r["domain"] for r in rows}

    # 3d window should include it
    result = _invoke(runner, ["monetization", "list-recent", "--since", "3d"])
    assert result.exit_code == 0, result.output
    rows = json.loads(result.stdout)
    assert "fresh-auction.com" in {r["domain"] for r in rows}


def test_list_recent_rejects_bad_since(patched_session, runner):
    result = _invoke(runner, ["monetization", "list-recent", "--since", "whenever"])
    assert result.exit_code != 0


# ─── _parse_since unit ───────────────────────────────────────────────


def test_parse_since_units():
    from pacer.cli.monetization import _parse_since

    now = datetime.now(UTC)
    for expr, expected_delta in [
        ("1 hour ago", timedelta(hours=1)),
        ("2 hours ago", timedelta(hours=2)),
        ("30m", timedelta(minutes=30)),
        ("30 min", timedelta(minutes=30)),
        ("1d", timedelta(days=1)),
        ("7 days ago", timedelta(days=7)),
    ]:
        parsed = _parse_since(expr)
        assert abs((now - parsed) - expected_delta) < timedelta(seconds=5), expr
