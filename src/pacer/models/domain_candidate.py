"""DomainCandidate — the central pipeline record."""

from __future__ import annotations

import enum
from datetime import date, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from pacer.models.base import Base, TimestampMixin


class PipelineSource(str, enum.Enum):
    PACER_RECAP = "pacer_recap"
    SOS_DISSOLUTION = "sos_dissolution"
    EDGAR = "edgar"
    USPTO = "uspto"
    UCC_LIEN = "ucc_lien"
    PROBATE = "probate"


class Status(str, enum.Enum):
    DISCOVERED = "discovered"
    ENRICHED = "enriched"
    SCORED = "scored"
    QUEUED_DROPCATCH = "queued_dropcatch"
    CAUGHT = "caught"
    TOKENIZED = "tokenized"
    MONETIZED = "monetized"
    DISCARDED = "discarded"
    FAILED = "failed"


class DomainCandidate(Base, TimestampMixin):
    __tablename__ = "domain_candidates"
    __table_args__ = (
        UniqueConstraint("domain", name="uq_domain_candidates_domain"),
        Index("ix_domain_candidates_status_score", "status", "score"),
        Index("ix_domain_candidates_pending_delete", "pending_delete_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # Core identity
    domain: Mapped[str] = mapped_column(String(253), nullable=False)
    company_name: Mapped[str | None] = mapped_column(String(512))

    # Provenance
    source: Mapped[PipelineSource] = mapped_column(Enum(PipelineSource), nullable=False)
    source_record_id: Mapped[str | None] = mapped_column(String(256))
    source_payload: Mapped[dict | None] = mapped_column(JSON)

    # Compliance
    llc_entity: Mapped[str] = mapped_column(String(128), nullable=False, default="1COMMERCE LLC")

    # Lifecycle
    status: Mapped[Status] = mapped_column(Enum(Status), nullable=False, default=Status.DISCOVERED)

    # Scoring
    score: Mapped[float | None] = mapped_column(Float)
    domain_rating: Mapped[float | None] = mapped_column(Float)
    backlinks: Mapped[int | None] = mapped_column(Integer)
    referring_domains: Mapped[int | None] = mapped_column(Integer)
    topical_relevance: Mapped[float | None] = mapped_column(Float)
    spam_score: Mapped[float | None] = mapped_column(Float)

    # Drop-catch timing
    expiration_date: Mapped[date | None] = mapped_column(Date)
    pending_delete_date: Mapped[date | None] = mapped_column(Date)
    caught_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    caught_by_registrar: Mapped[str | None] = mapped_column(String(64))

    # RWA
    rwa_token_id: Mapped[str | None] = mapped_column(String(128))
    rwa_type: Mapped[str | None] = mapped_column(String(16))  # "DOT" | "DST"
    securitize_offering_id: Mapped[str | None] = mapped_column(String(128))

    # Monetization
    monetization_strategy: Mapped[str | None] = mapped_column(String(64))
    redirect_target: Mapped[str | None] = mapped_column(String(512))
    revenue_to_date_cents: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # EPMV inputs (commercial intent signals used by composite yield score)
    cpc_usd: Mapped[float | None] = mapped_column(Float)
    est_monthly_searches: Mapped[int | None] = mapped_column(Integer)

    # Auction / lease-to-own
    auction_listing_url: Mapped[str | None] = mapped_column(String(512))
    lease_to_own_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    lease_monthly_price_cents: Mapped[int | None] = mapped_column(Integer)

    # Trademark screen result (set by USPTOTrademarkScreener)
    tm_conflict: Mapped[bool | None] = mapped_column(Boolean)
    tm_reason: Mapped[str | None] = mapped_column(String(64))

    # Partner attribution (1099 contractor / profit-share — see legal/)
    partner_id: Mapped[int | None] = mapped_column(
        ForeignKey("partners.id", ondelete="SET NULL"), nullable=True
    )
    partner_rev_share_pct: Mapped[float | None] = mapped_column(Float)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<DomainCandidate {self.domain} status={self.status} score={self.score}>"
