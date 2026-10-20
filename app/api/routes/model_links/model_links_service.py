from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routes.model_links.model import ProviderLink
from app.core.errors import NotFoundError, ValidationError
from app.services.routing import LinkRow

Slot = tuple[str, str]  # (role, placement)


async def ensure_links(
    session: AsyncSession,
    owner_id: str,
    project: str,
    slots: tuple[Slot, ...],
    defaults: dict[Slot, str],
) -> list[ProviderLink]:
    """Make sure the user has one row per slot for this project, pre-filled with
    the defaults from settings the first time."""
    rows = list(
        (
            await session.scalars(
                select(ProviderLink).where(
                    ProviderLink.owner_id == owner_id, ProviderLink.project == project
                )
            )
        ).all()
    )
    have = {(row.role, row.placement) for row in rows}
    changed = False
    for role, placement in slots:
        if (role, placement) in have:
            continue
        default = (defaults.get((role, placement)) or "").strip()
        session.add(
            ProviderLink(
                owner_id=owner_id,
                project=project,
                role=role,
                placement=placement,
                url=default,
                enabled=bool(default),
            )
        )
        changed = True
    if changed:
        await session.commit()
        rows = list(
            (
                await session.scalars(
                    select(ProviderLink).where(
                        ProviderLink.owner_id == owner_id, ProviderLink.project == project
                    )
                )
            ).all()
        )
    wanted = {slot: index for index, slot in enumerate(slots)}
    rows = [row for row in rows if (row.role, row.placement) in wanted]
    rows.sort(key=lambda row: wanted[(row.role, row.placement)])
    return rows


def _check_url(url: str) -> str:
    cleaned = url.strip().rstrip("/")
    if not cleaned:
        return ""
    if not cleaned.startswith(("http://", "https://")):
        raise ValidationError("A link must start with http:// or https://")
    if len(cleaned) > 500:
        raise ValidationError("That link is too long")
    return cleaned


async def update_link(
    session: AsyncSession, owner_id: str, project: str, link_id: uuid.UUID, url: str, enabled: bool
) -> ProviderLink:
    row = await session.scalar(
        select(ProviderLink).where(
            ProviderLink.id == link_id,
            ProviderLink.owner_id == owner_id,
            ProviderLink.project == project,
        )
    )
    if row is None:
        raise NotFoundError("Link not found")
    row.url = _check_url(url)
    if enabled and not row.url:
        raise ValidationError("Save a URL before turning the link on")
    row.enabled = enabled and bool(row.url)
    await session.commit()
    await session.refresh(row)
    return row


def as_rows(links: list[ProviderLink]) -> list[LinkRow]:
    return [
        LinkRow(role=link.role, placement=link.placement, url=link.url or "", enabled=link.enabled)
        for link in links
    ]
