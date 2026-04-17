"""DomainPortfolio — owned-domain portfolio record for 1COMMERCE LLC."""
from __future__ import annotations

from sqlalchemy import Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from pacer.models.base import Base, TimestampMixin


class DomainPortfolio(Base, TimestampMixin):
    """
    Owned domain portfolio entry for 1COMMERCE LLC.

    Tracks every domain we acquire (via drop-catch or direct purchase)
    including its ownership details, renewal timeline, estimated valuation,
    and the monetization strategy applied.
    """

    __tablename__ = "domain_portfolio"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    domain: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    registrar: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # ISO-8601 date strings (YYYY-MM-DD) for simplicity; avoids timezone ambiguity
    purchase_date: Mapped[str | None] = mapped_column(String(20), nullable=True)
    renewal_date: Mapped[str | None] = mapped_column(String(20), nullable=True)

    purchase_price_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    current_valuation_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    seo_score: Mapped[float | None] = mapped_column(Float, nullable=True)

    redirect_target: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # "301_redirect" | "parking" | "aftermarket"
    monetization_strategy: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # "active" | "expired" | "sold" | "pending"
    status: Mapped[str] = mapped_column(
        String(50), nullable=False, default="active", server_default="active"
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    def __init__(self, **kwargs: object) -> None:
        kwargs.setdefault("status", "active")
        super().__init__(**kwargs)

    def __repr__(self) -> str:
        return (
            f"<DomainPortfolio domain={self.domain!r} "
            f"status={self.status!r} valuation={self.current_valuation_usd}>"
        )
