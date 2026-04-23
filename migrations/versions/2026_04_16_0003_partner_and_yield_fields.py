"""partner table + yield/auction/TM/partner fields on domain_candidates

Revision ID: 0003_partner_yield
Revises: 0002_add_domain_portfolio
Create Date: 2026-04-16 00:00:01
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = "0003_partner_yield"
down_revision: Union[str, None] = "0002_add_domain_portfolio"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


partner_status_enum = sa.Enum(
    "prospect",
    "active",
    "paused",
    "terminated",
    name="partnerstatus",
)


def upgrade() -> None:

    # ── partners table ────────────────────────────────────────────
    op.create_table(
        "partners",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
            nullable=False,
        ),
        sa.Column("legal_name", sa.String(length=256), nullable=False),
        sa.Column("display_name", sa.String(length=128), nullable=True),
        sa.Column("email", sa.String(length=320), nullable=False, unique=True),
        sa.Column("tax_id_last4", sa.String(length=4), nullable=True),
        sa.Column("w9_received", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("w9_received_at", sa.Date(), nullable=True),
        sa.Column("state", sa.String(length=2), nullable=True),
        sa.Column("rev_share_pct", sa.Float(), nullable=False, server_default="20.0"),
        sa.Column("revenue_cap_cents", sa.Integer(), nullable=True),
        sa.Column(
            "status",
            partner_status_enum,
            nullable=False,
            server_default="prospect",
        ),
        sa.Column("agreement_signed_at", sa.Date(), nullable=True),
        sa.Column("terminated_at", sa.Date(), nullable=True),
        sa.Column("notes", sa.String(length=2048), nullable=True),
        sa.CheckConstraint(
            "rev_share_pct >= 0 AND rev_share_pct <= 24.9",
            name="ck_partners_rev_share_under_cta_threshold",
        ),
    )

    # ── domain_candidates: new columns ────────────────────────────
    with op.batch_alter_table("domain_candidates") as batch:
        batch.add_column(sa.Column("cpc_usd", sa.Float(), nullable=True))
        batch.add_column(sa.Column("est_monthly_searches", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("auction_listing_url", sa.String(length=512), nullable=True))
        batch.add_column(
            sa.Column(
                "lease_to_own_enabled",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch.add_column(sa.Column("lease_monthly_price_cents", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("tm_conflict", sa.Boolean(), nullable=True))
        batch.add_column(sa.Column("tm_reason", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("partner_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("partner_rev_share_pct", sa.Float(), nullable=True))
        batch.create_foreign_key(
            "fk_domain_candidates_partner_id",
            "partners",
            ["partner_id"],
            ["id"],
            ondelete="SET NULL",
        )

    op.create_index(
        "ix_domain_candidates_partner_id",
        "domain_candidates",
        ["partner_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_domain_candidates_partner_id", table_name="domain_candidates")

    with op.batch_alter_table("domain_candidates") as batch:
        batch.drop_constraint("fk_domain_candidates_partner_id", type_="foreignkey")
        batch.drop_column("partner_rev_share_pct")
        batch.drop_column("partner_id")
        batch.drop_column("tm_reason")
        batch.drop_column("tm_conflict")
        batch.drop_column("lease_monthly_price_cents")
        batch.drop_column("lease_to_own_enabled")
        batch.drop_column("auction_listing_url")
        batch.drop_column("est_monthly_searches")
        batch.drop_column("cpc_usd")

    op.drop_table("partners")

    bind = op.get_bind()
    partner_status_enum.drop(bind, checkfirst=True)
