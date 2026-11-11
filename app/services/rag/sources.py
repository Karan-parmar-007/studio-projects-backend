"""Source versions: create, look up, switch the live version, delete (Garage first)."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.garage import delete_prefix, garage_client
from app.services.rag import media
from app.services.rag.model import RagSource
from app.services.rag.retrieve import forget_graph

logger = logging.getLogger(__name__)

PUBLIC_SCOPES = ("chatbot", "book")


def now() -> datetime:
    return datetime.now(UTC)


async def books(session: AsyncSession, *, live_only: bool = True) -> list[RagSource]:
    query = select(RagSource).where(RagSource.scope == "book")
    if live_only:
        query = query.where(RagSource.active.is_(True), RagSource.status == "ready")
    return list(await session.scalars(query.order_by(RagSource.created_at)))


async def uploads(session: AsyncSession) -> list[RagSource]:
    return list(
        await session.scalars(select(RagSource).where(RagSource.scope == "upload").order_by(RagSource.created_at.desc()))
    )


async def exists(session: AsyncSession, source_id: uuid.UUID) -> bool:
    return bool(await session.scalar(text("SELECT 1 FROM studio.rag_sources WHERE id = :id"), {"id": source_id}))


async def put_on_shelf(session: AsyncSession, source: RagSource) -> None:
    await session.execute(text("UPDATE studio.rag_sources SET active = true WHERE id = :id"), {"id": source.id})
    await session.commit()


async def active(session: AsyncSession, scope: str) -> RagSource | None:
    return await session.scalar(select(RagSource).where(RagSource.scope == scope, RagSource.active.is_(True)))


async def building(session: AsyncSession, scope: str) -> RagSource | None:
    """A chatbot/book version that is still being made (not yet live, not failed)."""
    return await session.scalar(
        select(RagSource)
        .where(
            RagSource.scope == scope,
            RagSource.active.is_(False),
            RagSource.status.in_(("queued", "processing", "ready")),
        )
        .order_by(RagSource.created_at.desc())
    )


async def versions(session: AsyncSession, scope: str, limit: int = 10) -> list[RagSource]:
    rows = await session.scalars(
        select(RagSource).where(RagSource.scope == scope).order_by(RagSource.created_at.desc()).limit(limit)
    )
    return list(rows)


async def upload_of(session: AsyncSession, owner_id: str) -> RagSource | None:
    return await session.scalar(
        select(RagSource).where(RagSource.scope == "upload", RagSource.owner_id == owner_id)
    )


async def set_stage(session: AsyncSession, source_id: uuid.UUID, stage: str, progress: int, **extra) -> None:
    values = {"stage": stage[:40], "progress": max(0, min(100, progress)), **extra}
    await session.execute(update(RagSource).where(RagSource.id == source_id).values(**values))
    await session.commit()


async def go_live(session: AsyncSession, source: RagSource) -> list[uuid.UUID]:
    """Chatbot only. Switch the live version to `source` in one transaction. Returns the ids
    of the versions it replaced, which the caller deletes afterwards."""
    await session.execute(
        text("UPDATE studio.rag_sources SET active = false WHERE scope = :scope AND active AND id <> :id"),
        {"scope": source.scope, "id": source.id},
    )
    await session.execute(
        text("UPDATE studio.rag_sources SET active = true WHERE id = :id"), {"id": source.id}
    )
    await session.commit()
    rows = await session.execute(
        text(
            """
            SELECT id FROM studio.rag_sources
            WHERE scope = :scope AND id <> :id AND NOT active AND created_at <= :created
            """
        ),
        {"scope": source.scope, "id": source.id, "created": source.created_at},
    )
    return [row[0] for row in rows]


async def delete_source(session: AsyncSession, source_id: uuid.UUID) -> None:
    """Images and the original file first, then the rows (chunks, figures, entities, jobs and
    cached answers cascade). Safe to run twice: a half-finished delete is finished next time."""
    try:
        async with garage_client() as client:
            removed = await delete_prefix(client, media.storage_prefix(source_id))
        logger.info("deleted %s stored objects of source %s", removed, source_id)
    except Exception:  # noqa: BLE001
        # Without Garage (dev) or on a transient error: the sweeper retries rows it can still see.
        logger.exception("could not delete stored files of source %s", source_id)
        raise
    await session.execute(text("DELETE FROM studio.rag_sources WHERE id = :id"), {"id": source_id})
    await session.commit()
    forget_graph(source_id)
