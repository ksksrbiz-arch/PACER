"""payout_entries ledger table

Revision ID: 0004_payout_ledger
Revises: 0003_partner_yield
Create Date: 2026-04-17
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0004_payout_ledger"
down_revision = "0003_partner_yield"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "payout_entries",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "partner_id",
            sa.Integer(),
            sa.ForeignKey("partners.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "domain_candidate_id",
            sa.Integer(),
            sa.ForeignKey("domain_candidates.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("domain", sa.String(length=253), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("gross_revenue_cents", sa.Integer(), nullable=False),
        sa.Column("partner_cents", sa.Integer(), nullable=False),
        sa.Column("llc_cents", sa.Integer(), nullable=False),
        sa.Column("rev_share_pct", sa.Float(), nullable=False),
        sa.Column(
            "status",
            sa.Enum("pending", "paid", "voided", name="payoutstatus"),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("paid_at", sa.Date(), nullable=True),
        sa.Column("payment_ref", sa.String(length=128), nullable=True),
        sa.Column("notes", sa.String(length=1024), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "rev_share_pct >= 0 AND rev_share_pct <= 24.9",
            name="ck_payout_entries_rev_share_under_cta_threshold",
        ),
        sa.CheckConstraint(
            "gross_revenue_cents >= 0 AND partner_cents >= 0 AND llc_cents >= 0",
            name="ck_payout_entries_non_negative",
        ),
        sa.CheckConstraint(
            "period_end >= period_start",
            name="ck_payout_entries_period_order",
        ),
    )
    op.create_index(
        "ix_payout_entries_partner_period",
        "payout_entries",
        ["partner_id", "period_start", "period_end"],
    )
    op.create_index(
        "ix_payout_entries_status",
        "payout_entries",
        ["status"],
    )


def downgrade() -> None:
    op.drop_index("ix_payout_entries_status", table_name="payout_entries")
    op.drop_index("ix_payout_entries_partner_period", table_name="payout_entries")
    op.drop_table("payout_entries")
    op.execute("DROP TYPE IF EXISTS payoutstatus")
