"""Initial schema — domain_candidates and compliance_logs tables.

Revision ID: 0001_initial
Revises:
Create Date: 2026-04-16
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "domain_candidates",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("company_name", sa.String(255), nullable=False),
        sa.Column("domain", sa.String(255), nullable=True),
        sa.Column("case_id", sa.String(100), nullable=True),
        sa.Column("filing_date", sa.String(20), nullable=True),
        sa.Column("source", sa.String(50), nullable=False, server_default="pacer_pcl"),
        sa.Column("seo_score", sa.Float(), nullable=True),
        sa.Column("topical_score", sa.Float(), nullable=True),
        sa.Column("funding_history", sa.JSON(), nullable=True),
        sa.Column("drop_catch_status", sa.String(50), nullable=True),
        sa.Column("rwa_token_id", sa.String(255), nullable=True),
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

    op.create_table(
        "compliance_logs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "llc_entity", sa.String(100), nullable=False, server_default="1COMMERCE LLC"
        ),
        sa.Column("event", sa.String(100), nullable=False),
        sa.Column("details", sa.JSON(), nullable=True),
        sa.Column("source", sa.String(50), nullable=False, server_default="PACER"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
    )


def downgrade() -> None:
    op.drop_table("compliance_logs")
    op.drop_table("domain_candidates")
