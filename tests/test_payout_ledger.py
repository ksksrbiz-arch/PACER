"""PayoutLedger tests — in-memory SQLite, no real Postgres.

Covers the record_batch → mark_paid → void lifecycle plus DB-level CHECK
constraint enforcement for the CTA cap. We build a tiny async SQLite
engine per test so nothing bleeds between cases.
"""
from __future__ import annotations

from datetime import date

import pytest
import pytest_asyncio
from sqlalchemy import event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from pacer.models.base import Base
from pacer.partners.ledger import PayoutEntry, PayoutLedger, PayoutStatus
from pacer.partners.models.partner import Partner, PartnerStatus
from pacer.partners.payout import PayoutLine, compute_payout

# SQLite doesn't enforce CHECK constraints on older builds by default in all
# drivers; pysqlite does enforce them since Python 3.x bundles a modern
# sqlite. We still register a PRAGMA foreign_keys=ON hook to match prod FK
# behavior.


def _enable_sqlite_fks(dbapi_conn, _):
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA foreign_keys=ON")
    cur.close()


@pytest_asyncio.fixture
async def session() -> AsyncSession:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    event.listen(engine.sync_engine, "connect", _enable_sqlite_fks)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s

    await engine.dispose()


@pytest_asyncio.fixture
async def partner(session: AsyncSession) -> Partner:
    p = Partner(
        legal_name="Jane Contractor",
        email="jane@example.com",
        rev_share_pct=20.0,
        status=PartnerStatus.ACTIVE,
    )
    session.add(p)
    await session.commit()
    await session.refresh(p)
    return p


# --- build_entry ---------------------------------------------------------
@pytest.mark.asyncio
async def test_build_entry_materializes_line(
    session: AsyncSession, partner: Partner
) -> None:
    ledger = PayoutLedger(session)
    line = compute_payout(partner.id, "widget.com", 10_000)  # $100
    entry = ledger.build_entry(
        line, period_start=date(2026, 4, 1), period_end=date(2026, 4, 30)
    )
    assert entry.domain == "widget.com"
    assert entry.partner_cents == 2000  # 20% of $100
    assert entry.llc_cents == 8000
    assert entry.status == PayoutStatus.PENDING


@pytest.mark.asyncio
async def test_build_entry_rejects_inverted_period(
    session: AsyncSession, partner: Partner
) -> None:
    ledger = PayoutLedger(session)
    line = compute_payout(partner.id, "x.com", 100)
    with pytest.raises(ValueError):
        ledger.build_entry(
            line, period_start=date(2026, 4, 30), period_end=date(2026, 4, 1)
        )


# --- record_batch --------------------------------------------------------
@pytest.mark.asyncio
async def test_record_batch_persists_entries(
    session: AsyncSession, partner: Partner
) -> None:
    ledger = PayoutLedger(session)
    lines = [
        compute_payout(partner.id, "a.com", 5_000),
        compute_payout(partner.id, "b.com", 7_500),
    ]
    entries = await ledger.record_batch(
        lines,
        period_start=date(2026, 4, 1),
        period_end=date(2026, 4, 30),
    )
    await session.commit()

    assert len(entries) == 2
    assert all(e.id is not None for e in entries)
    assert all(e.status == PayoutStatus.PENDING for e in entries)


@pytest.mark.asyncio
async def test_record_batch_wires_candidate_fk(
    session: AsyncSession, partner: Partner
) -> None:
    from pacer.models.domain_candidate import (
        DomainCandidate,
        PipelineSource,
    )

    cand = DomainCandidate(
        domain="linked.com",
        source=PipelineSource.SOS_DISSOLUTION,
        llc_entity="1COMMERCE LLC",
    )
    session.add(cand)
    await session.commit()
    await session.refresh(cand)

    ledger = PayoutLedger(session)
    line = compute_payout(partner.id, "linked.com", 12_345)
    entries = await ledger.record_batch(
        [line],
        period_start=date(2026, 4, 1),
        period_end=date(2026, 4, 30),
        candidate_id_by_domain={"linked.com": cand.id},
    )
    await session.commit()
    assert entries[0].domain_candidate_id == cand.id


# --- mark_paid -----------------------------------------------------------
@pytest.mark.asyncio
async def test_mark_paid_transitions_state(
    session: AsyncSession, partner: Partner
) -> None:
    ledger = PayoutLedger(session)
    line = compute_payout(partner.id, "x.com", 1_000)
    [entry] = await ledger.record_batch(
        [line], period_start=date(2026, 4, 1), period_end=date(2026, 4, 30)
    )
    await session.commit()

    await ledger.mark_paid(entry, paid_on=date(2026, 5, 5), payment_ref="ACH-42")
    await session.commit()

    assert entry.status == PayoutStatus.PAID
    assert entry.paid_at == date(2026, 5, 5)
    assert entry.payment_ref == "ACH-42"


@pytest.mark.asyncio
async def test_mark_paid_rejects_non_pending(
    session: AsyncSession, partner: Partner
) -> None:
    ledger = PayoutLedger(session)
    line = compute_payout(partner.id, "x.com", 1_000)
    [entry] = await ledger.record_batch(
        [line], period_start=date(2026, 4, 1), period_end=date(2026, 4, 30)
    )
    await session.commit()
    await ledger.mark_paid(entry, paid_on=date(2026, 5, 1), payment_ref="ACH-1")
    await session.commit()

    with pytest.raises(ValueError, match="cannot mark"):
        await ledger.mark_paid(entry, paid_on=date(2026, 5, 2), payment_ref="ACH-2")


# --- void ----------------------------------------------------------------
@pytest.mark.asyncio
async def test_void_pending_entry(
    session: AsyncSession, partner: Partner
) -> None:
    ledger = PayoutLedger(session)
    line = compute_payout(partner.id, "x.com", 1_000)
    [entry] = await ledger.record_batch(
        [line], period_start=date(2026, 4, 1), period_end=date(2026, 4, 30)
    )
    await session.commit()

    await ledger.void(entry, reason="duplicate")
    await session.commit()

    assert entry.status == PayoutStatus.VOIDED
    assert "VOID: duplicate" in (entry.notes or "")


@pytest.mark.asyncio
async def test_void_rejects_paid_entry(
    session: AsyncSession, partner: Partner
) -> None:
    ledger = PayoutLedger(session)
    line = compute_payout(partner.id, "x.com", 1_000)
    [entry] = await ledger.record_batch(
        [line], period_start=date(2026, 4, 1), period_end=date(2026, 4, 30)
    )
    await session.commit()
    await ledger.mark_paid(entry, paid_on=date(2026, 5, 1), payment_ref="ACH-1")
    await session.commit()

    with pytest.raises(ValueError, match="reversal"):
        await ledger.void(entry, reason="too late")


# --- DB-level CHECK constraints ------------------------------------------
@pytest.mark.asyncio
async def test_rev_share_cta_cap_enforced_at_db(
    session: AsyncSession, partner: Partner
) -> None:
    # Bypass compute_payout's validation to test the DB check directly.
    bad = PayoutEntry(
        partner_id=partner.id,
        domain="cap.com",
        period_start=date(2026, 4, 1),
        period_end=date(2026, 4, 30),
        gross_revenue_cents=1000,
        partner_cents=500,
        llc_cents=500,
        rev_share_pct=50.0,  # > 24.9% cap
    )
    session.add(bad)
    with pytest.raises(IntegrityError):
        await session.commit()
