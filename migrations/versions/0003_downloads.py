"""App binaries catalog (bytes live in Garage).

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-20

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "studio"


def upgrade() -> None:
    op.create_table(
        "downloads",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("product", sa.String(length=40), nullable=False),
        sa.Column("platform", sa.String(length=20), nullable=False),
        sa.Column("filename", sa.String(length=200), nullable=False),
        sa.Column("content_type", sa.String(length=100), nullable=False),
        sa.Column("size", sa.BigInteger(), nullable=False),
        sa.Column("version", sa.String(length=40), nullable=False),
        sa.Column("object_key", sa.String(length=300), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("product", "platform", name="uq_downloads_product_platform"),
        schema=SCHEMA,
    )
    op.create_index("ix_studio_downloads_product", "downloads", ["product"], schema=SCHEMA)


def downgrade() -> None:
    op.drop_table("downloads", schema=SCHEMA)
