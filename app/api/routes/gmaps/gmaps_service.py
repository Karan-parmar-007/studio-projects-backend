"""Maps Scraper runs: quota, places, and the streamed scrape.

A run lives in memory (one uvicorn worker). It is settled exactly once, by a
supervisor task that does not depend on the HTTP request: whatever the user did
not receive is refunded, even if they disconnected or never read the stream.
"""

from __future__ import annotations

import asyncio
import json
import logging
import queue
import threading
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from functools import lru_cache
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routes.gmaps import gmaps_constants as constants
from app.api.routes.gmaps.gmaps_schemas import RunIn
from app.api.routes.gmaps.model import GmapsDailyUsage
from app.api.routes.services import service_switches
from app.core.errors import ConflictError, QuotaError, ValidationError
from app.services.gmaps.locations import build_zip_pool, list_cities, list_countries, list_states
from app.services.gmaps.runner import ScraperRunner

logger = logging.getLogger(__name__)

PROJECT = constants.PROJECT
CLOSED_MID_RUN = "The Maps Scraper was switched off. Your unused companies were given back."


def today() -> date:
    return datetime.now(UTC).date()


# ---------------------------------------------------------------- quota


async def quota(session: AsyncSession, user_id: str) -> dict:
    day = today()
    used = await session.scalar(
        select(GmapsDailyUsage.companies_used).where(
            GmapsDailyUsage.user_id == user_id, GmapsDailyUsage.day == day
        )
    )
    used = int(used or 0)
    limit = constants.DAILY_COMPANY_LIMIT
    return {"limit": limit, "used": used, "remaining": max(0, limit - used), "day": day.isoformat()}


async def reserve(session: AsyncSession, user_id: str, day: date, want: int | None) -> int:
    """Take up to `want` (or everything left) from today's quota. Returns how many were taken."""
    limit = constants.DAILY_COMPANY_LIMIT
    await session.execute(
        insert(GmapsDailyUsage)
        .values(user_id=user_id, day=day, companies_used=0)
        .on_conflict_do_nothing(index_elements=["user_id", "day"])
    )
    row = await session.scalar(
        select(GmapsDailyUsage)
        .where(GmapsDailyUsage.user_id == user_id, GmapsDailyUsage.day == day)
        .with_for_update()
    )
    remaining = limit - int(row.companies_used)
    if remaining <= 0:
        await session.rollback()
        raise QuotaError(
            f"You have used all {limit} companies for today. Try again tomorrow (UTC).",
            code="quota_exceeded",
        )
    granted = min(want or remaining, remaining)
    row.companies_used = int(row.companies_used) + granted
    await session.commit()
    return granted


async def refund(session: AsyncSession, user_id: str, day: date, amount: int) -> None:
    if amount <= 0:
        return
    await session.execute(
        update(GmapsDailyUsage)
        .where(GmapsDailyUsage.user_id == user_id, GmapsDailyUsage.day == day)
        .values(companies_used=func.greatest(GmapsDailyUsage.companies_used - amount, 0))
    )
    await session.commit()


async def refund_detached(factory: Any, user_id: str, day: date, amount: int) -> None:
    """Refund in a fresh session; the request's session is gone by the time a run settles."""
    if amount <= 0 or factory is None:
        return
    async for session in factory.get_session():
        await refund(session, user_id, day, amount)


# ---------------------------------------------------------------- places


@lru_cache(maxsize=1)
def countries() -> tuple[str, ...]:
    return tuple(list_countries())


@lru_cache(maxsize=1)
def states() -> tuple[str, ...]:
    return tuple(list_states())


@lru_cache(maxsize=128)
def _cities(chosen: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(list_cities(list(chosen) or None))


def cities(chosen_states: list[str] | None = None) -> tuple[str, ...]:
    return _cities(tuple(sorted({s.strip().lower() for s in chosen_states or [] if s.strip()})))


@lru_cache(maxsize=1)
def _state_codes() -> frozenset[str]:
    return frozenset(loc.state_abbr.lower() for loc in build_zip_pool(shuffle=False) if loc.state_abbr)


def _unknown(asked: list[str], known: set[str]) -> list[str]:
    return [item for item in asked if item.lower() not in known]


def check_run(body: RunIn) -> RunIn:
    """Reject places we do not have, and filters that match no ZIP code at all."""
    problems: list[str] = []
    missing = _unknown(body.countries, {c.lower() for c in countries()})
    if missing:
        problems.append("Unknown country: " + ", ".join(missing[:5]))
    state_names = {s.lower() for s in states()}
    missing = _unknown(body.states, state_names | _state_codes())
    if missing:
        problems.append("Unknown state: " + ", ".join(missing[:5]))
    missing = _unknown(body.cities, {c.lower() for c in cities(body.states)})
    if missing:
        where = " in the chosen states" if body.states else ""
        problems.append(f"Unknown city{where}: " + ", ".join(missing[:5]))
    if problems:
        raise ValidationError(". ".join(problems))
    if not build_zip_pool(
        countries=body.countries or None,
        states=body.states or None,
        cities=body.cities or None,
        shuffle=False,
    ):
        raise ValidationError("No ZIP codes match those places")
    return body


# ---------------------------------------------------------------- runs


def _event(kind: str, **data: Any) -> str:
    return json.dumps({"type": kind, "data": data}, default=str) + "\n"


def _row_of(company: Any) -> dict:
    return company.to_dict() if hasattr(company, "to_dict") else dict(company)


def _key_of(row: dict) -> str:
    return str(row.get("place_id") or row.get("cid") or f"{row.get('name')}|{row.get('phone')}")


@dataclass
class Run:
    id: str
    user_id: str
    day: date
    reserved: int
    body: RunIn
    events: queue.Queue = field(default_factory=queue.Queue)
    stop: threading.Event = field(default_factory=threading.Event)
    attached: threading.Event = field(default_factory=threading.Event)
    consumer_done: threading.Event = field(default_factory=threading.Event)
    worker_done: threading.Event = field(default_factory=threading.Event)
    emitted: int = 0  # handed to the stream by the engine thread
    delivered: int = 0  # written to the client by the stream
    seen: set[str] = field(default_factory=set)
    worker: threading.Thread | None = None


class RunRegistry:
    """At most one run per user and MAX_ACTIVE_RUNS overall. A claimed slot holds None."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._runs: dict[str, Run | None] = {}

    def active(self) -> int:
        with self._lock:
            return len(self._runs)

    def claim(self, user_id: str) -> None:
        with self._lock:
            if user_id in self._runs:
                raise ConflictError("A Maps Scraper run is already going for this account")
            if len(self._runs) >= constants.MAX_ACTIVE_RUNS:
                raise QuotaError(
                    "The Maps Scraper is busy with other searches. Try again in a minute.",
                    code="busy",
                )
            self._runs[user_id] = None

    def attach(self, run: Run) -> None:
        with self._lock:
            self._runs[run.user_id] = run

    def release(self, user_id: str, run_id: str | None = None) -> None:
        with self._lock:
            current = self._runs.get(user_id)
            if run_id is None or (current is not None and current.id == run_id):
                self._runs.pop(user_id, None)

    def stop_all(self, message: str = CLOSED_MID_RUN) -> None:
        with self._lock:
            runs = [run for run in self._runs.values() if run is not None]
        for run in runs:
            run.stop.set()
            run.events.put(("error", {"message": message}))
            run.events.put(None)


registry = RunRegistry()
service_switches.on_close(PROJECT, registry.stop_all)

# Supervisors must outlive the request; keep strong references so they are not collected.
_supervisors: set[asyncio.Task] = set()


def _offer(run: Run, row: dict) -> bool:
    """Queue one company unless it is a duplicate or the run is full. False once full."""
    key = _key_of(row)
    if key in run.seen:
        return True
    if run.emitted >= run.reserved:
        run.stop.set()
        return False
    run.seen.add(key)
    run.emitted += 1
    run.events.put(("company", {"company": row}))
    return True


def _work(run: Run) -> None:
    """Engine thread. Never touches the database."""
    try:

        def on_progress(info: dict) -> None:
            if run.stop.is_set():
                return
            for company in info.get("companies_preview") or []:
                if not _offer(run, _row_of(company)):
                    break
            run.events.put(
                (
                    "progress",
                    {
                        "status": str(info.get("status") or ""),
                        "companiesFound": int(info.get("companies_found") or run.emitted),
                        "emitted": run.emitted,
                        "limit": run.reserved,
                    },
                )
            )

        runner = ScraperRunner(
            search_term=run.body.search_term,
            countries=list(run.body.countries),
            states=list(run.body.states),
            cities=list(run.body.cities),
            limit=run.reserved,
            per_zip_cap=constants.PER_ZIP_CAP,
            max_discovery_workers=constants.DISCOVERY_WORKERS,
            on_progress=on_progress,
            should_stop=run.stop.is_set,
        )
        for company in runner.run() or []:
            if run.stop.is_set() or not _offer(run, _row_of(company)):
                break
        total = run.emitted
        stopped = run.stop.is_set() and total < run.reserved
        run.events.put(
            (
                "completed",
                {
                    "status": "stopped" if stopped else "completed",
                    "message": (
                        f"Stopped with {total:,} companies."
                        if stopped
                        else f"Collected {total:,} companies." if total else "No companies were found."
                    ),
                    "companiesTotal": total,
                    "finishedAt": datetime.now(UTC).isoformat(),
                },
            )
        )
    except Exception:  # noqa: BLE001
        logger.exception("Maps Scraper run %s failed", run.id)
        run.events.put(("error", {"message": "The search failed. Unused companies were given back."}))
    finally:
        run.worker_done.set()
        run.events.put(None)


async def _wait(event: threading.Event, timeout: float | None = None) -> bool:
    """Wait for a thread event without holding a thread-pool thread for the whole run."""
    loop = asyncio.get_running_loop()
    deadline = None if timeout is None else loop.time() + timeout
    while not event.is_set():
        if deadline is not None and loop.time() >= deadline:
            return False
        await asyncio.sleep(0.1)
    return True


async def _supervise(run: Run, factory: Any) -> None:
    """Settle the run exactly once: stop orphans, wait for the engine, refund, free the slot."""
    try:
        if not await _wait(run.attached, constants.ATTACH_TIMEOUT_SECONDS):
            run.stop.set()
        await _wait(run.worker_done)
        if run.attached.is_set():
            await _wait(run.consumer_done, constants.CONSUMER_GRACE_SECONDS)
        # A stream still writing after the grace period is charged for everything it was handed.
        charged = run.delivered if run.consumer_done.is_set() or not run.attached.is_set() else run.emitted
        owed = run.reserved - min(max(charged, 0), run.reserved)
        try:
            await refund_detached(factory, run.user_id, run.day, owed)
        except Exception:  # noqa: BLE001
            logger.exception("Maps Scraper refund failed for run %s", run.id)
    finally:
        registry.release(run.user_id, run.id)


async def _stream(run: Run) -> AsyncIterator[str]:
    run.attached.set()
    try:
        yield _event(
            "started",
            runId=run.id,
            limit=run.reserved,
            workers=constants.DISCOVERY_WORKERS,
            perZipCap=constants.PER_ZIP_CAP,
        )
        while True:
            try:
                item = await asyncio.to_thread(run.events.get, True, 0.5)
            except queue.Empty:
                if run.worker_done.is_set() and run.events.empty():
                    break
                continue
            if item is None:
                break
            kind, payload = item
            if kind == "company":
                run.delivered += 1
            yield _event(kind, **payload)
    finally:
        # Only thread-safe flags here: this also runs when the client disconnects.
        run.stop.set()
        run.consumer_done.set()


async def start_run(session: AsyncSession, factory: Any, user_id: str, body: RunIn) -> AsyncIterator[str]:
    body = check_run(body)
    await service_switches.assert_open(session, PROJECT)
    registry.claim(user_id)
    day = today()
    try:
        granted = await reserve(session, user_id, day, body.limit)
    except BaseException:
        registry.release(user_id)
        raise
    run = Run(id=str(uuid.uuid4()), user_id=user_id, day=day, reserved=granted, body=body)
    registry.attach(run)
    run.worker = threading.Thread(target=_work, args=(run,), name=f"gmaps-{run.id[:8]}", daemon=True)
    run.worker.start()
    task = asyncio.create_task(_supervise(run, factory))
    _supervisors.add(task)
    task.add_done_callback(_supervisors.discard)
    return _stream(run)
