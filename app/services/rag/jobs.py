"""A small job queue in Postgres for studio-worker. No Redis, no Celery.

Two worker lanes so a six-hour book never blocks a user's 5 MB upload:
  fast  user uploads
  slow  chatbot knowledge and the book
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

MAX_ATTEMPTS = 3
# A job still "running" after this long belonged to a worker that died.
STALE_MINUTES = 240


@dataclass
class Job:
    id: int
    kind: str
    lane: str
    source_id: uuid.UUID | None
    attempts: int
    payload: dict


async def enqueue(
    session: AsyncSession,
    kind: str,
    source_id: uuid.UUID | None,
    *,
    lane: str,
    priority: int = 5,
    payload: dict | None = None,
    commit: bool = True,
) -> None:
    await session.execute(
        text(
            """
            INSERT INTO studio.rag_jobs (kind, lane, source_id, priority, payload)
            VALUES (:kind, :lane, :source_id, :priority, CAST(:payload AS jsonb))
            """
        ),
        {
            "kind": kind,
            "lane": lane,
            "source_id": source_id,
            "priority": priority,
            "payload": json.dumps(payload or {}),
        },
    )
    if commit:
        await session.commit()


async def claim(session: AsyncSession, lane: str) -> Job | None:
    row = (
        await session.execute(
            text(
                """
                UPDATE studio.rag_jobs SET status = 'running', locked_at = now(),
                       attempts = attempts + 1, updated_at = now()
                WHERE id = (
                    SELECT id FROM studio.rag_jobs
                    WHERE status = 'queued' AND lane = :lane AND run_after <= now()
                    ORDER BY priority, id
                    FOR UPDATE SKIP LOCKED LIMIT 1
                )
                RETURNING id, kind, lane, source_id, attempts, payload
                """
            ),
            {"lane": lane},
        )
    ).first()
    await session.commit()
    if row is None:
        return None
    return Job(id=int(row[0]), kind=row[1], lane=row[2], source_id=row[3], attempts=int(row[4]), payload=row[5] or {})


async def finish(session: AsyncSession, job: Job) -> None:
    await session.execute(
        text("UPDATE studio.rag_jobs SET status = 'done', updated_at = now() WHERE id = :id"), {"id": job.id}
    )
    await session.commit()


async def fail(session: AsyncSession, job: Job, error: str) -> bool:
    """Retry with backoff until MAX_ATTEMPTS. True when it will run again."""
    retry = job.attempts < MAX_ATTEMPTS
    await session.execute(
        text(
            """
            UPDATE studio.rag_jobs SET
                status = CASE WHEN :retry THEN 'queued' ELSE 'failed' END,
                run_after = now() + make_interval(mins => :backoff),
                error = :error, locked_at = NULL, updated_at = now()
            WHERE id = :id
            """
        ),
        {"id": job.id, "retry": retry, "backoff": 2 * job.attempts, "error": error[:2000]},
    )
    await session.commit()
    return retry


async def recover_stale(session: AsyncSession, *, everything: bool = False) -> int:
    """At worker start nothing can be running (one worker): requeue it all. Later, only
    jobs whose lock is older than STALE_MINUTES."""
    condition = "status = 'running'" if everything else (
        f"status = 'running' AND locked_at < now() - interval '{STALE_MINUTES} minutes'"
    )
    result = await session.execute(
        text(f"UPDATE studio.rag_jobs SET status = 'queued', locked_at = NULL WHERE {condition}")
    )
    await session.execute(
        text("DELETE FROM studio.rag_jobs WHERE status IN ('done', 'failed') AND updated_at < now() - interval '7 days'")
    )
    await session.commit()
    return int(result.rowcount or 0)
