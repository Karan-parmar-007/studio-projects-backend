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
            connect_args={
                "server_settings": {
                    "timezone": "UTC",
                    "search_path": db_settings.POSTGRES_SCHEMA,
                }
            },
        )
        self._sessionmaker: async_sessionmaker[AsyncSession] = async_sessionmaker(
            bind=self._engine,
            expire_on_commit=False,
            class_=AsyncSession,
        )

    async def verify_connection(self) -> None:
        async with self._sessionmaker() as session:
            await session.execute(text("SELECT 1"))

    async def get_session(self) -> AsyncGenerator[AsyncSession, None]:
        async with self._sessionmaker() as session:
            yield session

    async def dispose(self) -> None:
        await self._engine.dispose()
