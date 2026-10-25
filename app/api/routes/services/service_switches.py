"""A single on/off switch for a whole product. Missing row means on."""

from __future__ import annotations

from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routes.services.model import ServiceSwitch
from app.core.errors import ServiceClosedError

CONTACT_EMAIL = "karan.ai.engineer@gmail.com"
CLOSED_MESSAGE = (
    f"This service is off right now. Contact Karan at {CONTACT_EMAIL}."
)

# Products with work in flight (e.g. a running scrape) register here to stop it when switched off.
_ON_CLOSE: dict[str, list[Callable[[], None]]] = {}


def on_close(slug: str, hook: Callable[[], None]) -> None:
    _ON_CLOSE.setdefault(slug, []).append(hook)


async def enabled_map(session: AsyncSession) -> dict[str, bool]:
    if not isinstance(session, AsyncSession):
        return {}
    rows = (await session.scalars(select(ServiceSwitch))).all()
    return {row.project: bool(row.enabled) for row in rows}


async def is_enabled(session: AsyncSession, slug: str) -> bool:
    flags = await enabled_map(session)
    return flags.get(slug, True)


async def set_enabled(session: AsyncSession, slug: str, enabled: bool) -> bool:
    row = await session.get(ServiceSwitch, slug)
    if row is None:
        session.add(ServiceSwitch(project=slug, enabled=enabled))
    else:
        row.enabled = enabled
    await session.commit()
    if not enabled:
        for hook in _ON_CLOSE.get(slug, []):
            hook()
    return enabled


async def assert_open(session: AsyncSession, slug: str) -> None:
    if not await is_enabled(session, slug):
        raise ServiceClosedError(CLOSED_MESSAGE)
