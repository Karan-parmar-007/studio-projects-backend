from collections.abc import AsyncGenerator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import db_settings


class PostgresSession:
    def __init__(self) -> None:
        self._engine: AsyncEngine = create_async_engine(
            db_settings.postgres_url,
            echo=db_settings.POSTGRES_ECHO,
            # A Postgres restart (image change, upgrade) kills pooled connections; check each one
            # before use and replace dead ones instead of answering 500 until the app restarts.
            pool_pre_ping=True,
            connect_args={
                "server_settings": {
                    "timezone": "UTC",
                    # public too: the pgvector extension's types and operators live there.
                    "search_path": f"{db_settings.POSTGRES_SCHEMA},public",
                }
            },
        )
        self._sessionmaker: async_sessionmaker[AsyncSession] = async_sessionmaker(
            bind=self._engine,
            expire_on_commit=False,
            class_=AsyncSession,
        )

    @property
    def sessionmaker(self) -> async_sessionmaker[AsyncSession]:
        """For work outside a request (worker, stream settlement): `async with pg.sessionmaker() as s`."""
        return self._sessionmaker

    async def verify_connection(self) -> None:
        async with self._sessionmaker() as session:
            await session.execute(text("SELECT 1"))

    async def get_session(self) -> AsyncGenerator[AsyncSession, None]:
        async with self._sessionmaker() as session:
            yield session

    async def dispose(self) -> None:
        await self._engine.dispose()
