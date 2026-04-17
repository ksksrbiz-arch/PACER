from datetime import datetime

from sqlalchemy import JSON, DateTime, Float, Integer, String, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class DomainCandidate(Base):
    """A distressed SaaS/tech domain surfaced from PACER or other pipelines."""

    __tablename__ = "domain_candidates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    company_name: Mapped[str] = mapped_column(String(255), nullable=False)
    domain: Mapped[str | None] = mapped_column(String(255), nullable=True)
    case_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    filing_date: Mapped[str | None] = mapped_column(String(20), nullable=True)
    source: Mapped[str] = mapped_column(
        String(50), nullable=False, default="pacer_pcl", server_default="pacer_pcl"
    )
    seo_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    topical_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    funding_history: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    drop_catch_status: Mapped[str | None] = mapped_column(String(50), nullable=True)
    rwa_token_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    def __init__(self, **kwargs: object) -> None:
        kwargs.setdefault("source", "pacer_pcl")
        super().__init__(**kwargs)

    def __repr__(self) -> str:
        return f"<DomainCandidate company={self.company_name!r} domain={self.domain!r} score={self.seo_score}>"


class ComplianceLog(Base):
    """Audit trail for 1COMMERCE LLC — DFR exemption, tax, Canby license renewal."""

    __tablename__ = "compliance_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    llc_entity: Mapped[str] = mapped_column(
        String(100), nullable=False, default="1COMMERCE LLC", server_default="1COMMERCE LLC"
    )
    event: Mapped[str] = mapped_column(String(100), nullable=False)
    details: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    source: Mapped[str] = mapped_column(
        String(50), nullable=False, default="PACER", server_default="PACER"
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    def __repr__(self) -> str:
        return f"<ComplianceLog event={self.event!r} entity={self.llc_entity!r}>"

    def __init__(self, **kwargs: object) -> None:
        kwargs.setdefault("llc_entity", "1COMMERCE LLC")
        kwargs.setdefault("source", "PACER")
        super().__init__(**kwargs)


class DomainPortfolio(Base):
    """
    Owned domain portfolio entry for 1COMMERCE LLC.

    Tracks every domain we acquire (via drop-catch or direct purchase) including
    its ownership details, renewal timeline, valuation, and monetization status.
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
    monetization_strategy: Mapped[str | None] = mapped_column(
        String(50), nullable=True
    )  # "301_redirect" | "parking" | "aftermarket"
    status: Mapped[str] = mapped_column(
        String(50), nullable=False, default="active", server_default="active"
    )  # "active" | "expired" | "sold" | "pending"
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    def __init__(self, **kwargs: object) -> None:
        kwargs.setdefault("status", "active")
        super().__init__(**kwargs)

    def __repr__(self) -> str:
        return (
            f"<DomainPortfolio domain={self.domain!r} "
            f"status={self.status!r} valuation={self.current_valuation_usd}>"
        )
