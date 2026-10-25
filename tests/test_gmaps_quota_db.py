"""Quota against the real Postgres (studio schema). Skipped when the database is unreachable."""

from __future__ import annotations

import asyncio
import uuid
from datetime import date

import pytest
from sqlalchemy import delete

from app.api.routes.gmaps import gmaps_constants, gmaps_service
from app.api.routes.gmaps.model import GmapsDailyUsage
from app.core.errors import QuotaError
from app.db.session import PostgresSession

DAY = date(2000, 1, 1)


@pytest.fixture
async def db():
    manager = PostgresSession()
    try:
        await asyncio.wait_for(manager.verify_connection(), 3)
    except Exception:
        await manager.dispose()
        pytest.skip("Postgres is not reachable")
    user = f"test-gmaps-{uuid.uuid4().hex[:12]}"
    yield manager, user
    async for session in manager.get_session():
        await session.execute(delete(GmapsDailyUsage).where(GmapsDailyUsage.user_id == user))
        await session.commit()
    await manager.dispose()


async def _reserve(manager, user, want):
    async for session in manager.get_session():
        return await gmaps_service.reserve(session, user, DAY, want)


async def _used(manager, user):
    async for session in manager.get_session():
        row = await session.get(GmapsDailyUsage, (user, DAY))
        return 0 if row is None else row.companies_used


async def test_reserve_and_refund_round_trip(db):
    manager, user = db
    assert await _reserve(manager, user, 10) == 10
    assert await _reserve(manager, user, None) == gmaps_constants.DAILY_COMPANY_LIMIT - 10
    with pytest.raises(QuotaError):
        await _reserve(manager, user, 1)
    await gmaps_service.refund_detached(manager, user, DAY, 15)
    assert await _used(manager, user) == gmaps_constants.DAILY_COMPANY_LIMIT - 15


async def test_refund_never_goes_below_zero(db):
    manager, user = db
    await _reserve(manager, user, 3)
    await gmaps_service.refund_detached(manager, user, DAY, 999)
    assert await _used(manager, user) == 0


async def test_parallel_reserves_never_exceed_the_daily_limit(db):
    manager, user = db
    results = await asyncio.gather(
        *[_reserve(manager, user, 20) for _ in range(5)], return_exceptions=True
    )
    granted = sum(r for r in results if isinstance(r, int))
    assert granted == gmaps_constants.DAILY_COMPANY_LIMIT
    assert all(isinstance(r, (int, QuotaError)) for r in results)
    assert await _used(manager, user) == gmaps_constants.DAILY_COMPANY_LIMIT
