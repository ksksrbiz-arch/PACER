"""initial schema: domain_candidates + compliance_log

Revision ID: 0001_initial
Revises:
Create Date: 2026-04-16 00:00:00
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers
revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


pipeline_source_enum = sa.Enum(
    "pacer_recap",
    "sos_dissolution",
    "edgar",
    "uspto",
    "ucc_lien",
    "probate",
    name="pipelinesource",
)

candidate_status_enum = sa.Enum(
    "discovered",
    "enriched",
    "scored",
    "queued_dropcatch",
    "caught",
    "tokenized",
    "monetized",
    "discarded",
    "failed",
    name="status",
)

event_severity_enum = sa.Enum(
    "info",
    "warning",
    "error",
    "critical",
    name="eventseverity",
)


def upgrade() -> None:
    bind = op.get_bind()
    pipeline_source_enum.create(bind, checkfirst=True)
    candidate_status_enum.create(bind, checkfirst=True)
    event_severity_enum.create(bind, checkfirst=True)

    op.create_table(
        "domain_candidates",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
            nullable=False,
        ),
        sa.Column("domain", sa.String(length=253), nullable=False),
        sa.Column("company_name", sa.String(length=512), nullable=True),
        sa.Column("source", pipeline_source_enum, nullable=False),
        sa.Column("source_record_id", sa.String(length=256), nullable=True),
        sa.Column("source_payload", postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column("llc_entity", sa.String(length=128), nullable=False, server_default="1COMMERCE LLC"),
        sa.Column("status", candidate_status_enum, nullable=False, server_default="discovered"),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("domain_rating", sa.Float(), nullable=True),
        sa.Column("backlinks", sa.Integer(), nullable=True),
        sa.Column("referring_domains", sa.Integer(), nullable=True),
        sa.Column("topical_relevance", sa.Float(), nullable=True),
        sa.Column("spam_score", sa.Float(), nullable=True),
        sa.Column("expiration_date", sa.Date(), nullable=True),
        sa.Column("pending_delete_date", sa.Date(), nullable=True),
        sa.Column("caught_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("caught_by_registrar", sa.String(length=64), nullable=True),
        sa.Column("rwa_token_id", sa.String(length=128), nullable=True),
        sa.Column("rwa_type", sa.String(length=16), nullable=True),
        sa.Column("securitize_offering_id", sa.String(length=128), nullable=True),
        sa.Column("monetization_strategy", sa.String(length=64), nullable=True),
        sa.Column("redirect_target", sa.String(length=512), nullable=True),
        sa.Column("revenue_to_date_cents", sa.Integer(), nullable=False, server_default="0"),
        sa.UniqueConstraint("domain", name="uq_domain_candidates_domain"),
    )
    op.create_index(
        "ix_domain_candidates_status_score",
        "domain_candidates",
        ["status", "score"],
    )
    op.create_index(
        "ix_domain_candidates_pending_delete",
        "domain_candidates",
        ["pending_delete_date"],
    )

    op.create_table(
        "compliance_log",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
            nullable=False,
        ),
        sa.Column("llc_entity", sa.String(length=128), nullable=False),
        sa.Column("event_type", sa.String(length=128), nullable=False),
        sa.Column("severity", event_severity_enum, nullable=False, server_default="info"),
        sa.Column("endpoint", sa.String(length=512), nullable=True),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("domain", sa.String(length=253), nullable=True),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("payload", postgresql.JSON(astext_type=sa.Text()), nullable=True),
    )
    op.create_index("ix_compliance_log_event_type", "compliance_log", ["event_type"])
    op.create_index("ix_compliance_log_severity", "compliance_log", ["severity"])


def downgrade() -> None:
    op.drop_index("ix_compliance_log_severity", table_name="compliance_log")
    op.drop_index("ix_compliance_log_event_type", table_name="compliance_log")
    op.drop_table("compliance_log")

    op.drop_index("ix_domain_candidates_pending_delete", table_name="domain_candidates")
    op.drop_index("ix_domain_candidates_status_score", table_name="domain_candidates")
    op.drop_table("domain_candidates")

    bind = op.get_bind()
    event_severity_enum.drop(bind, checkfirst=True)
    candidate_status_enum.drop(bind, checkfirst=True)
    pipeline_source_enum.drop(bind, checkfirst=True)
