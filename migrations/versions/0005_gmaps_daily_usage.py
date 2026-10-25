"""Maps Scraper daily company quota.

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-25

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "studio"


def upgrade() -> None:
    # (user_id, day) is the primary key: one row per user per UTC day, found by index.
    op.create_table(
        "gmaps_daily_usage",
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("companies_used", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("user_id", "day"),
        sa.CheckConstraint("companies_used >= 0", name="ck_gmaps_daily_usage_non_negative"),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_table("gmaps_daily_usage", schema=SCHEMA)
