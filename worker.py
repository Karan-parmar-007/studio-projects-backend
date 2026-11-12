"""studio-worker: document ingestion and the expiry sweeper.

    python worker.py

Same code and database as the API, its own container (it carries Docling, which
the API image does not). Three loops:
  fast   user uploads, one at a time
  slow   chatbot knowledge + the book, one at a time (can take hours)
  sweep  deletes expired uploads every RAG_SWEEP_SECONDS
"""

from __future__ import annotations

import asyncio
import logging
import signal

from app.config import rag_settings
from app.db.session import PostgresSession
from app.services import llm_client
from app.services.rag import ingest, jobs, sweeper

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("worker")

IDLE_SECONDS = 3.0
HANDLERS = {"ingest": ingest.run}


async def work_lane(maker, lane: str, stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            async with maker() as session:
                job = await jobs.claim(session, lane)
        except Exception:  # noqa: BLE001
            logger.exception("%s: could not claim a job", lane)
            job = None
        if job is None:
            try:
                await asyncio.wait_for(stop.wait(), IDLE_SECONDS)
            except TimeoutError:
                pass
            continue
        handler = HANDLERS.get(job.kind)
        logger.info("%s: job %s (%s) for %s, attempt %s", lane, job.id, job.kind, job.source_id, job.attempts)
        try:
            if handler is None:
                raise RuntimeError(f"unknown job kind {job.kind}")
            await handler(maker, job.source_id)
            async with maker() as session:
                await jobs.finish(session, job)
        except Exception as exc:  # noqa: BLE001
            logger.exception("%s: job %s failed", lane, job.id)
            async with maker() as session:
                await jobs.fail(session, job, f"{type(exc).__name__}: {exc}")


async def sweep_loop(maker, stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            await sweeper.sweep(maker)
            async with maker() as session:
                await jobs.recover_stale(session)
        except Exception:  # noqa: BLE001
            logger.exception("sweep failed")
        try:
            await asyncio.wait_for(stop.wait(), rag_settings.RAG_SWEEP_SECONDS)
        except TimeoutError:
            pass


async def main() -> None:
    postgres = PostgresSession()
    maker = postgres.sessionmaker
    llm_client.use_sessions(maker)  # figure descriptions use the PC first when it is up
    async with maker() as session:
        recovered = await jobs.recover_stale(session, everything=True)
    if recovered:
        logger.info("requeued %s jobs left running by a previous worker", recovered)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass
    logger.info("worker started")
    await asyncio.gather(
        work_lane(maker, "fast", stop),
        work_lane(maker, "slow", stop),
        sweep_loop(maker, stop),
    )
    await postgres.dispose()


if __name__ == "__main__":
    asyncio.run(main())
