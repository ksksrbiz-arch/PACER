"""Partner — 1099-NEC contractor operating under 1COMMERCE LLC.

Not an equity holder. Not a beneficial owner for CTA/BOI purposes
(rev_share_pct capped at 24.9). See legal/partner_profit_share_agreement.md.
"""
from __future__ import annotations

import enum
from datetime import date

from sqlalchemy import Boolean, CheckConstraint, Date, Enum, Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from pacer.models.base import Base, TimestampMixin


class PartnerStatus(str, enum.Enum):
    PROSPECT = "prospect"
    ACTIVE = "active"
    PAUSED = "paused"
    TERMINATED = "terminated"


class Partner(Base, TimestampMixin):
    __tablename__ = "partners"
    __table_args__ = (
        CheckConstraint(
            "rev_share_pct >= 0 AND rev_share_pct <= 24.9",
            name="ck_partners_rev_share_under_cta_threshold",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # Identity
    legal_name: Mapped[str] = mapped_column(String(256), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(128))
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True)

    # Tax / contractor
    tax_id_last4: Mapped[str | None] = mapped_column(String(4))  # never store full SSN/EIN
    w9_received: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    w9_received_at: Mapped[date | None] = mapped_column(Date)
    state: Mapped[str | None] = mapped_column(String(2))

    # Economic terms
    rev_share_pct: Mapped[float] = mapped_column(Float, nullable=False, default=20.0)
    revenue_cap_cents: Mapped[int | None] = mapped_column(Integer)  # optional annual cap

    # Lifecycle
    status: Mapped[PartnerStatus] = mapped_column(
        Enum(PartnerStatus), nullable=False, default=PartnerStatus.PROSPECT
    )
    agreement_signed_at: Mapped[date | None] = mapped_column(Date)
    terminated_at: Mapped[date | None] = mapped_column(Date)

    # Ops
    notes: Mapped[str | None] = mapped_column(String(2048))

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Partner {self.email} {self.status} share={self.rev_share_pct}>"
