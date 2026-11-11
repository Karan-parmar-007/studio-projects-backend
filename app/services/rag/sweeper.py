"""Delete what has expired. Runs in studio-worker every RAG_SWEEP_SECONDS (10 min).

  uploads    expires_at (completion + TTL) has passed
  uploads    never completed within HARD_CEILING of being created (stuck or failed)
  old chatbot versions that a crash left behind (not live, older than the live one)
Books are never swept: the admin adds and deletes them.

Garage objects go first, then rows, so a crash in between is finished next run.
Queries already refuse expired uploads, so a late sweep never leaks an answer.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from sqlalchemy import text

from app.services.rag import sources as source_lib

logger = logging.getLogger(__name__)

HARD_CEILING = timedelta(hours=3)
FAILED_GRACE = timedelta(minutes=30)


async def sweep(maker) -> int:
    async with maker() as session:
        rows = await session.execute(
            text(
                """
                SELECT id FROM studio.rag_sources
                WHERE scope = 'upload' AND (
                      (expires_at IS NOT NULL AND expires_at < now())
                   OR (completed_at IS NULL AND created_at < now() - make_interval(secs => :ceiling))
                   OR (status = 'failed' AND updated_at < now() - make_interval(secs => :grace))
                )
                UNION
                SELECT s.id FROM studio.rag_sources s
                JOIN studio.rag_sources live ON live.scope = s.scope AND live.active
                WHERE s.scope = 'chatbot' AND NOT s.active AND s.created_at < live.created_at
                """
            ),
            {"ceiling": HARD_CEILING.total_seconds(), "grace": FAILED_GRACE.total_seconds()},
        )
        doomed = [row[0] for row in rows]
    removed = 0
    for source_id in doomed:
        async with maker() as session:
            try:
                await source_lib.delete_source(session, source_id)
                removed += 1
            except Exception:  # noqa: BLE001
                logger.exception("sweep: could not delete source %s (will retry)", source_id)
    if removed:
        logger.info("sweep: deleted %s expired sources", removed)
    return removed
