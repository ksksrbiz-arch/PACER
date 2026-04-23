"""Structured audit log tied to 1COMMERCE LLC."""

from __future__ import annotations

import enum

from sqlalchemy import JSON, BigInteger, Enum, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from pacer.models.base import Base, TimestampMixin


class EventSeverity(str, enum.Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class ComplianceLog(Base, TimestampMixin):
    __tablename__ = "compliance_log"
    __table_args__ = (
        Index("ix_compliance_log_event_type", "event_type"),
        Index("ix_compliance_log_severity", "severity"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    llc_entity: Mapped[str] = mapped_column(String(128), nullable=False)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    severity: Mapped[EventSeverity] = mapped_column(
        Enum(EventSeverity), nullable=False, default=EventSeverity.INFO
    )

    endpoint: Mapped[str | None] = mapped_column(String(512))
    http_status: Mapped[int | None] = mapped_column(Integer)

    domain: Mapped[str | None] = mapped_column(String(253))
    message: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[dict | None] = mapped_column(JSON)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<ComplianceLog {self.event_type} {self.severity} llc={self.llc_entity}>"
