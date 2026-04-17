"""``pacer partners payout`` CLI — end-to-end with in-memory SQLite.

We patch ``pacer.cli.partners.session_scope`` to yield sessions from a shared
aiosqlite engine so every call in the same test hits the same DB. The CLI is
driven via Click's :class:`CliRunner` so we exercise the real argv wiring.
"""
from __future__ import annotations

import csv
from datetime import date
from pathlib import Path

import pytest
import pytest_asyncio
from click.testing import CliRunner
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from pacer.models.base import Base
from pacer.models.domain_candidate import (
    DomainCandidate,
    PipelineSource,
    Status,
)
from pacer.partners.ledger import PayoutEntry, PayoutStatus
from pacer.partners.models.partner import Partner, PartnerStatus


def _enable_sqlite_fks(dbapi_conn, _):
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA foreign_keys=ON")
    cur.close()


@pytest_asyncio.fixture
async def engine():
    eng = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    event.listen(eng.sync_engine, "connect", _enable_sqlite_fks)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def seeded(engine):
    """Seed partner + two candidates with revenue attribution."""
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        partner = Partner(
            legal_name="Jane Contractor",
            email="jane@example.com",
            tax_id_last4="1234",
            state="OR",
            rev_share_pct=20.0,
            status=PartnerStatus.ACTIVE,
        )
        s.add(partner)
        await s.commit()
        await s.refresh(partner)

        # Candidate 1: $10k revenue, 20% share → partner=$2k, llc=$8k
        c1 = DomainCandidate(
            domain="widget.com",
            company_name="Widget Co",
            source=PipelineSource.SOS_DISSOLUTION,
            llc_entity="1COMMERCE LLC",
            status=Status.MONETIZED,
            revenue_to_date_cents=1_000_000,  # $10,000
            partner_id=partner.id,
            partner_rev_share_pct=20.0,
        )
        # Candidate 2: $200 revenue → partner=$40, llc=$160
        c2 = DomainCandidate(
            domain="gadget.io",
            company_name="Gadget Inc",
            source=PipelineSource.SOS_DISSOLUTION,
            llc_entity="1COMMERCE LLC",
            status=Status.MONETIZED,
            revenue_to_date_cents=20_000,  # $200
            partner_id=partner.id,
            partner_rev_share_pct=20.0,
        )
        # Candidate 3: no partner → ignored
        c3 = DomainCandidate(
            domain="orphan.net",
            source=PipelineSource.SOS_DISSOLUTION,
            llc_entity="1COMMERCE LLC",
            revenue_to_date_cents=50_000,
        )
        s.add_all([c1, c2, c3])
        await s.commit()
        return partner


@pytest.fixture
def patched_session(monkeypatch, engine):
    """Patch session_scope used by the CLI module to use our test engine."""
    from contextlib import asynccontextmanager

    maker = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def _scope():
        async with maker() as s:
            yield s

    monkeypatch.setattr("pacer.cli.partners.session_scope", _scope)
    return _scope


@pytest.fixture
def runner():
    return CliRunner()


# ─────────────────────────── helpers ────────────────────────────────
def _invoke(runner: CliRunner, args: list[str], cwd: Path):
    from pacer.main import cli

    with runner.isolated_filesystem(temp_dir=cwd):
        result = runner.invoke(cli, args, catch_exceptions=False)
        return result


# ─────────────────────────── run (dry) ──────────────────────────────
@pytest.mark.asyncio
async def test_payout_run_dry_run_does_not_persist(
    seeded, patched_session, runner, tmp_path, engine
):
    result = _invoke(runner, ["partners", "payout", "run", "--period", "2026-04", "--dry-run"], tmp_path)
    assert result.exit_code == 0, result.output
    assert "DRY-RUN" in result.output
    assert "Entries:           2" in result.output
    # $2000 + $40 = $2040 partner total
    assert "$2,040.00" in result.output

    # Verify nothing was persisted
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        from sqlalchemy import select
        entries = list((await s.execute(select(PayoutEntry))).scalars().all())
    assert entries == []


# ─────────────────────────── run (persist) ──────────────────────────
@pytest.mark.asyncio
async def test_payout_run_persists_and_writes_csv(
    seeded, patched_session, runner, tmp_path, engine
):
    result = _invoke(runner, ["partners", "payout", "run", "--period", "2026-04"], tmp_path)
    assert result.exit_code == 0, result.output
    assert "PERSISTED" in result.output

    # Ledger row in DB
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        from sqlalchemy import select
        entries = list((await s.execute(select(PayoutEntry))).scalars().all())
    assert len(entries) == 2
    assert sum(e.partner_cents for e in entries) == 204_000  # $2,040
    assert all(e.status == PayoutStatus.PENDING for e in entries)

    # CSV written to reports/payouts/2026-04/ledger_2026-04.csv
    # (relative to CliRunner's isolated filesystem)
    # We can't easily inspect it post-exit because the isolated dir is
    # cleaned up — but the summary line confirms it wrote. The next test
    # asserts CSV content directly by bypassing CliRunner isolation.


@pytest.mark.asyncio
async def test_payout_run_ledger_csv_content(
    seeded, patched_session, tmp_path, engine, monkeypatch
):
    """Run against a real cwd so we can read the CSV back."""
    monkeypatch.chdir(tmp_path)
    from pacer.cli.partners import _run_payout

    summary = await _run_payout("2026-04", dry_run=False)
    csv_path = Path(summary["ledger_csv"])
    assert csv_path.exists()

    with csv_path.open() as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 2
    domains = {r["domain"] for r in rows}
    assert domains == {"widget.com", "gadget.io"}
    # Every row has rev_share_pct 20.00
    assert all(r["rev_share_pct"] == "20.00" for r in rows)


# ─────────────────────────── 1099-NEC threshold ─────────────────────
@pytest.mark.asyncio
async def test_1099nec_csv_includes_partner_above_600(
    seeded, patched_session, tmp_path, engine, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    from pacer.cli.partners import _mark_paid, _run_payout

    # Run + mark-paid so the entries count toward YTD 1099 totals.
    summary = await _run_payout("2026-04", dry_run=False)

    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        from sqlalchemy import select
        entries = list((await s.execute(select(PayoutEntry))).scalars().all())

    for e in entries:
        await _mark_paid(e.id, ref=f"ACH-{e.id}", paid_on=date(2026, 5, 5))

    # Re-run to regenerate the 1099 CSV now that entries are PAID.
    summary2 = await _run_payout("2026-05", dry_run=False)
    nec_path = Path(summary2["nec_csv"])
    assert nec_path.exists()
    with nec_path.open() as f:
        rows = list(csv.DictReader(f))
    # Jane got $2,040 YTD — above $600 threshold, so she's on the 1099 list.
    assert len(rows) == 1
    assert rows[0]["legal_name"] == "Jane Contractor"
    assert rows[0]["tax_id_last4"] == "1234"
    assert int(rows[0]["ytd_nonemployee_compensation_cents"]) == 204_000


@pytest.mark.asyncio
async def test_1099nec_csv_excludes_partner_below_600(
    engine, patched_session, tmp_path, monkeypatch
):
    """A partner with only $100 YTD does NOT appear on the 1099 roster."""
    monkeypatch.chdir(tmp_path)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        p = Partner(
            legal_name="Tiny Partner",
            email="tiny@example.com",
            rev_share_pct=20.0,
            status=PartnerStatus.ACTIVE,
        )
        s.add(p)
        await s.commit()
        await s.refresh(p)
        c = DomainCandidate(
            domain="small.biz",
            source=PipelineSource.SOS_DISSOLUTION,
            llc_entity="1COMMERCE LLC",
            revenue_to_date_cents=50_000,  # $500, 20% share → $100 partner
            partner_id=p.id,
            partner_rev_share_pct=20.0,
        )
        s.add(c)
        await s.commit()

    from pacer.cli.partners import _mark_paid, _run_payout

    summary = await _run_payout("2026-04", dry_run=False)
    async with maker() as s:
        from sqlalchemy import select
        [entry] = list((await s.execute(select(PayoutEntry))).scalars().all())
    await _mark_paid(entry.id, ref="ACH-x", paid_on=date(2026, 5, 1))

    summary2 = await _run_payout("2026-05", dry_run=False)
    with Path(summary2["nec_csv"]).open() as f:
        rows = list(csv.DictReader(f))
    assert rows == []  # below threshold, no 1099 needed


# ─────────────────────────── list + mark-paid ───────────────────────
@pytest.mark.asyncio
async def test_payout_list_and_mark_paid(
    seeded, patched_session, tmp_path, engine, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    from pacer.cli.partners import _list_payouts, _mark_paid, _run_payout

    await _run_payout("2026-04", dry_run=False)
    rows = await _list_payouts("2026-04", status=None)
    assert len(rows) == 2
    assert all(r["status"] == "pending" for r in rows)

    target_id = rows[0]["id"]
    await _mark_paid(target_id, ref="ACH-001", paid_on=date(2026, 5, 3))

    paid_rows = await _list_payouts("2026-04", status="paid")
    assert len(paid_rows) == 1
    assert paid_rows[0]["id"] == target_id


# ─────────────────────────── idempotency ────────────────────────────
@pytest.mark.asyncio
async def test_payout_run_second_time_zero_delta(
    seeded, patched_session, tmp_path, engine, monkeypatch
):
    """Re-running after a successful period should produce no new entries
    (prior period's gross is subtracted from running total)."""
    monkeypatch.chdir(tmp_path)
    from pacer.cli.partners import _run_payout

    await _run_payout("2026-04", dry_run=False)
    summary = await _run_payout("2026-05", dry_run=False)
    assert summary["entry_count"] == 0


@pytest.mark.asyncio
async def test_payout_run_same_period_rerun_is_idempotent(
    seeded, patched_session, tmp_path, engine, monkeypatch
):
    """Re-running the SAME period must not double-book.

    Regression guard: pre-fix predicate was `period_end < period_start` which
    excluded same-month entries from the prior-sum, so re-running April after
    persisting April would create a second set of identical ledger rows.
    """
    monkeypatch.chdir(tmp_path)
    from pacer.cli.partners import _run_payout

    first = await _run_payout("2026-04", dry_run=False)
    assert first["entry_count"] > 0

    second = await _run_payout("2026-04", dry_run=False)
    assert second["entry_count"] == 0
    assert second["total_partner_cents"] == 0


# ─────────────────────────── bad input ──────────────────────────────
def test_payout_run_bad_period(runner, tmp_path, patched_session):
    result = _invoke(runner, ["partners", "payout", "run", "--period", "not-a-period"], tmp_path)
    assert result.exit_code != 0
    assert "YYYY-MM" in result.output or "period" in result.output.lower()
