"""Partner payout ledger.

Persists :class:`pacer.partners.payout.PayoutLine` rows per billing cycle so
we have an auditable 1099-NEC trail. All amounts stored in cents.

Status lifecycle:
    pending  -- computed, not yet disbursed
    paid     -- ACH/check sent, external ref stored in ``payment_ref``
    voided   -- cancelled before payment (e.g. clawback, dispute)

The model is deliberately thin — pure ledger. Reconciliation against bank
statements happens in the finance pipeline, not here. Pointing to the
domain_candidate via FK lets us drop a reporting query like
``SELECT SUM(partner_cents) WHERE partner_id=? AND period_start>=?`` for
annual 1099-NEC summaries.
"""

from __future__ import annotations

import enum
from collections.abc import Iterable
from datetime import date

from sqlalchemy import (
    CheckConstraint,
    Date,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from pacer.models.base import Base, TimestampMixin
from pacer.partners.payout import PayoutLine


class PayoutStatus(str, enum.Enum):
    PENDING = "pending"
    PAID = "paid"
    VOIDED = "voided"


class PayoutEntry(Base, TimestampMixin):
    """One payout line per (partner, domain, billing period)."""

    __tablename__ = "payout_entries"
    __table_args__ = (
        CheckConstraint(
            "rev_share_pct >= 0 AND rev_share_pct <= 24.9",
            name="ck_payout_entries_rev_share_under_cta_threshold",
        ),
        CheckConstraint(
            "gross_revenue_cents >= 0 AND partner_cents >= 0 AND llc_cents >= 0",
            name="ck_payout_entries_non_negative",
        ),
        CheckConstraint(
            "period_end >= period_start",
            name="ck_payout_entries_period_order",
        ),
        Index(
            "ix_payout_entries_partner_period",
            "partner_id",
            "period_start",
            "period_end",
        ),
        Index("ix_payout_entries_status", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    partner_id: Mapped[int] = mapped_column(
        ForeignKey("partners.id", ondelete="RESTRICT"), nullable=False
    )
    domain_candidate_id: Mapped[int | None] = mapped_column(
        ForeignKey("domain_candidates.id", ondelete="SET NULL"), nullable=True
    )
    # Denormalized domain string — preserved even if candidate is deleted.
    domain: Mapped[str] = mapped_column(String(253), nullable=False)

    # Billing period (inclusive, monthly).
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)

    # Amounts (cents).
    gross_revenue_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    partner_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    llc_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    rev_share_pct: Mapped[float] = mapped_column(Float, nullable=False)

    # Lifecycle.
    status: Mapped[PayoutStatus] = mapped_column(
        Enum(PayoutStatus), nullable=False, default=PayoutStatus.PENDING
    )
    paid_at: Mapped[date | None] = mapped_column(Date)
    payment_ref: Mapped[str | None] = mapped_column(String(128))  # ACH txn id / check num
    notes: Mapped[str | None] = mapped_column(String(1024))

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<PayoutEntry partner={self.partner_id} domain={self.domain} "
            f"period={self.period_start}..{self.period_end} "
            f"{self.partner_cents}c {self.status}>"
        )


class PayoutLedger:
    """Thin persistence wrapper around :class:`PayoutEntry`.

    Designed so the caller owns the session — we don't open one here. Pass
    in a :class:`AsyncSession` and we'll add the rows; caller commits.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def build_entry(
        self,
        line: PayoutLine,
        period_start: date,
        period_end: date,
        domain_candidate_id: int | None = None,
    ) -> PayoutEntry:
        """Materialize a :class:`PayoutLine` into an unsaved :class:`PayoutEntry`."""
        if period_end < period_start:
            raise ValueError("period_end must be on/after period_start")
        return PayoutEntry(
            partner_id=line.partner_id,
            domain_candidate_id=domain_candidate_id,
            domain=line.domain,
            period_start=period_start,
            period_end=period_end,
            gross_revenue_cents=line.gross_revenue_cents,
            partner_cents=line.partner_cents,
            llc_cents=line.llc_cents,
            rev_share_pct=line.rev_share_pct,
            status=PayoutStatus.PENDING,
        )

    async def record_batch(
        self,
        lines: Iterable[PayoutLine],
        period_start: date,
        period_end: date,
        candidate_id_by_domain: dict[str, int] | None = None,
    ) -> list[PayoutEntry]:
        """Persist a batch of payout lines for one billing period.

        ``candidate_id_by_domain`` lets the caller wire the FK back to
        ``domain_candidates.id`` when it has the mapping in memory. Without
        it we still record the ledger row — just with a null FK.

        Returns the inserted entries (flushed, ids populated; caller commits).
        """
        lookup = candidate_id_by_domain or {}
        entries: list[PayoutEntry] = []
        for line in lines:
            entry = self.build_entry(
                line,
                period_start=period_start,
                period_end=period_end,
                domain_candidate_id=lookup.get(line.domain),
            )
            self._session.add(entry)
            entries.append(entry)
        await self._session.flush()
        return entries

    async def mark_paid(
        self,
        entry: PayoutEntry,
        paid_on: date,
        payment_ref: str,
    ) -> PayoutEntry:
        """Transition a ``pending`` entry to ``paid``.

        Raises ValueError on illegal transition (e.g. voided → paid).
        """
        if entry.status != PayoutStatus.PENDING:
            raise ValueError(f"cannot mark entry id={entry.id} paid from status={entry.status}")
        entry.status = PayoutStatus.PAID
        entry.paid_at = paid_on
        entry.payment_ref = payment_ref
        await self._session.flush()
        return entry

    async def void(self, entry: PayoutEntry, reason: str) -> PayoutEntry:
        """Void a pending entry (clawback, dispute, etc)."""
        if entry.status == PayoutStatus.PAID:
            raise ValueError(f"cannot void already-paid entry id={entry.id}; issue a reversal")
        entry.status = PayoutStatus.VOIDED
        entry.notes = (entry.notes or "") + f"\nVOID: {reason}"
        await self._session.flush()
        return entry
