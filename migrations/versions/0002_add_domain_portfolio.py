"""Add domain_portfolio table for 1COMMERCE LLC owned-domain tracking.

Revision ID: 0002_add_domain_portfolio
Revises: 0001_initial
Create Date: 2026-04-17
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002_add_domain_portfolio"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "domain_portfolio",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("domain", sa.String(255), nullable=False, unique=True),
        sa.Column("registrar", sa.String(100), nullable=True),
        sa.Column("purchase_date", sa.String(20), nullable=True),
        sa.Column("renewal_date", sa.String(20), nullable=True),
        sa.Column("purchase_price_usd", sa.Float(), nullable=True),
        sa.Column("current_valuation_usd", sa.Float(), nullable=True),
        sa.Column("seo_score", sa.Float(), nullable=True),
        sa.Column("redirect_target", sa.String(500), nullable=True),
        sa.Column("monetization_strategy", sa.String(50), nullable=True),
        sa.Column(
            "status",
            sa.String(50),
            nullable=False,
            server_default="active",
        ),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
    )


def downgrade() -> None:
    op.drop_table("domain_portfolio")
