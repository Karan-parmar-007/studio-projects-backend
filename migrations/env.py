"""Async Alembic env. Studio keeps its tables and its alembic_version in the
`studio` schema of the shared Postgres, so it never collides with the other
apps' migrations in `public`."""

import asyncio
from logging.config import fileConfig

from sqlalchemy import pool, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context
from app.config import db_settings
from app.db.base import Base

import app.api.routes.api_key.model  # noqa: F401
import app.api.routes.model_links.model  # noqa: F401
import app.api.routes.downloads.model  # noqa: F401
import app.api.routes.services.model  # noqa: F401
import app.api.routes.gmaps.model  # noqa: F401

config = context.config
config.set_main_option("sqlalchemy.url", db_settings.postgres_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata
SCHEMA = db_settings.POSTGRES_SCHEMA


def _only_studio(name, type_, parent_names) -> bool:
    """Autogenerate must ignore the other apps' tables in the shared database."""
    if type_ == "schema":
        return name == SCHEMA
    return True


def _configure(**kwargs) -> None:
    context.configure(
        target_metadata=target_metadata,
        version_table_schema=SCHEMA,
        include_schemas=True,
        include_name=_only_studio,
        **kwargs,
    )


def run_migrations_offline() -> None:
    _configure(
        url=config.get_main_option("sqlalchemy.url"),
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    connection.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{SCHEMA}"'))
    _configure(connection=connection)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
        await connection.commit()
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_async_migrations())
