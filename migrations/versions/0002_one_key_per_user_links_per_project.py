"""One API key per user (valid for every product); model links per project;
products are a static catalog in code, so the products/endpoints tables go.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-20

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "studio"


def upgrade() -> None:
    # --- api_keys: global per user, one live at a time (keep the newest)
    op.execute(
        f"""
        UPDATE {SCHEMA}.api_keys k SET revoked = true
        WHERE NOT k.revoked AND k.id <> (
            SELECT id FROM {SCHEMA}.api_keys n
            WHERE n.owner_id = k.owner_id AND NOT n.revoked
            ORDER BY n.created_at DESC LIMIT 1
        )
        """
    )
    op.drop_index("ix_studio_api_keys_product_id", table_name="api_keys", schema=SCHEMA)
    op.drop_column("api_keys", "product_id", schema=SCHEMA)
    op.create_index(
        "uq_api_keys_one_active_per_owner",
        "api_keys",
        ["owner_id"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("NOT revoked"),
    )

    # --- provider_links: scoped to a project
    op.add_column(
        "provider_links",
        sa.Column("project", sa.String(length=40), server_default="whisperflow", nullable=False),
        schema=SCHEMA,
    )
    op.drop_constraint("uq_provider_owner_role_place", "provider_links", schema=SCHEMA, type_="unique")
    op.create_unique_constraint(
        "uq_provider_owner_project_role_place",
        "provider_links",
        ["owner_id", "project", "role", "placement"],
        schema=SCHEMA,
    )
    # whisperflow has no server-side speech slot
    op.execute(
        f"DELETE FROM {SCHEMA}.provider_links WHERE role = 'speech' AND placement = 'remote'"
    )

    # --- products live in code now
    op.drop_table("api_endpoints", schema=SCHEMA)
    op.drop_table("api_products", schema=SCHEMA)


def downgrade() -> None:
    op.create_table(
        "api_products",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("slug", sa.String(length=140), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("owner_id", "slug", name="uq_product_owner_slug"),
        schema=SCHEMA,
    )
    op.create_index("ix_studio_api_products_owner_id", "api_products", ["owner_id"], schema=SCHEMA)
    op.create_table(
        "api_endpoints",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("operation", sa.String(length=40), nullable=False),
        sa.Column("method", sa.String(length=8), nullable=False),
        sa.Column("path", sa.String(length=80), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(["product_id"], [f"{SCHEMA}.api_products.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        schema=SCHEMA,
    )
    op.create_index("ix_studio_api_endpoints_product_id", "api_endpoints", ["product_id"], schema=SCHEMA)

    op.drop_constraint("uq_provider_owner_project_role_place", "provider_links", schema=SCHEMA, type_="unique")
    op.create_unique_constraint(
        "uq_provider_owner_role_place",
        "provider_links",
        ["owner_id", "role", "placement"],
        schema=SCHEMA,
    )
    op.drop_column("provider_links", "project", schema=SCHEMA)

    op.drop_index("uq_api_keys_one_active_per_owner", table_name="api_keys", schema=SCHEMA)
    op.add_column(
        "api_keys",
        sa.Column("product_id", postgresql.UUID(as_uuid=True), nullable=True),
        schema=SCHEMA,
    )
    op.create_index("ix_studio_api_keys_product_id", "api_keys", ["product_id"], schema=SCHEMA)
