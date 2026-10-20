from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routes.api_key.model import ApiKey
from app.core.errors import NotFoundError
from app.core.keys import hash_secret, new_api_secret, prefix_of

PREFIX = "sk-studio_"


async def current_key(session: AsyncSession, owner_id: str) -> ApiKey | None:
    return await session.scalar(
        select(ApiKey).where(ApiKey.owner_id == owner_id, ApiKey.revoked.is_(False))
    )


async def rotate_key(session: AsyncSession, owner_id: str, name: str) -> tuple[ApiKey, str]:
    """Revoke whatever key the user has and hand out a fresh one. The secret is returned once."""
    await session.execute(
        update(ApiKey)
        .where(ApiKey.owner_id == owner_id, ApiKey.revoked.is_(False))
        .values(revoked=True)
    )
    secret = new_api_secret()
    row = ApiKey(
        owner_id=owner_id,
        name=name.strip() or "Default",
        prefix=prefix_of(secret),
        key_hash=hash_secret(secret),
        revoked=False,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row, secret


async def revoke_key(session: AsyncSession, owner_id: str) -> None:
    row = await current_key(session, owner_id)
    if row is None:
        raise NotFoundError("You do not have a key")
    row.revoked = True
    await session.commit()


async def key_from_secret(session: AsyncSession, secret: str) -> ApiKey:
    if not secret.startswith(PREFIX):
        raise NotFoundError("Unknown API key")
    row = await session.scalar(
        select(ApiKey).where(ApiKey.key_hash == hash_secret(secret), ApiKey.revoked.is_(False))
    )
    if row is None:
        raise NotFoundError("Unknown API key")
    row.last_used_at = datetime.now(UTC)
    await session.commit()
    return row
