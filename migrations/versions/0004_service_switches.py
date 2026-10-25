"""One on/off switch per Studio product.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-25

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "studio"


def upgrade() -> None:
    op.create_table(
        "service_switches",
        sa.Column("project", sa.String(length=40), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("project"),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_table("service_switches", schema=SCHEMA)
