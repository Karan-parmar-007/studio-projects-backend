"""Initial Studio schema: products, endpoints, keys, provider links.

Matches app/db/models.py as of the first release. Everything lives in the
`studio` schema of the shared Postgres.

Revision ID: 0001
Revises:
Create Date: 2026-10-20

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "studio"


def upgrade() -> None:
    op.execute(f'CREATE SCHEMA IF NOT EXISTS "{SCHEMA}"')

    op.create_table(
        "api_products",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("slug", sa.String(length=140), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("owner_id", "slug", name="uq_product_owner_slug"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_studio_api_products_owner_id", "api_products", ["owner_id"], schema=SCHEMA
    )

    op.create_table(
        "api_endpoints",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("operation", sa.String(length=40), nullable=False),
        sa.Column("method", sa.String(length=8), nullable=False),
        sa.Column("path", sa.String(length=80), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(
            ["product_id"], [f"{SCHEMA}.api_products.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_studio_api_endpoints_product_id", "api_endpoints", ["product_id"], schema=SCHEMA
    )

    op.create_table(
        "api_keys",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("prefix", sa.String(length=24), nullable=False),
        sa.Column("key_hash", sa.String(length=64), nullable=False),
        sa.Column("revoked", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["product_id"], [f"{SCHEMA}.api_products.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        schema=SCHEMA,
    )
    op.create_index("ix_studio_api_keys_product_id", "api_keys", ["product_id"], schema=SCHEMA)
    op.create_index("ix_studio_api_keys_owner_id", "api_keys", ["owner_id"], schema=SCHEMA)
    op.create_index(
        "ix_studio_api_keys_key_hash", "api_keys", ["key_hash"], unique=True, schema=SCHEMA
    )

    op.create_table(
        "provider_links",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_id", sa.String(length=64), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("placement", sa.String(length=16), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("owner_id", "role", "placement", name="uq_provider_owner_role_place"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_studio_provider_links_owner_id", "provider_links", ["owner_id"], schema=SCHEMA
    )


def downgrade() -> None:
    op.drop_table("provider_links", schema=SCHEMA)
    op.drop_table("api_keys", schema=SCHEMA)
    op.drop_table("api_endpoints", schema=SCHEMA)
    op.drop_table("api_products", schema=SCHEMA)
