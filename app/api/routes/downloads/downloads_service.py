from __future__ import annotations

from botocore.client import BaseClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routes.downloads.downloads_constants import PLATFORMS, PRODUCTS, object_key
from app.api.routes.downloads.model import Download
from app.core.errors import NotFoundError, ValidationError
from app.db import garage


def _validate(product: str, platform: str) -> None:
    if product not in PRODUCTS:
        raise NotFoundError("Unknown product")
    if platform not in PLATFORMS:
        raise ValidationError(f"Platform must be one of: {', '.join(PLATFORMS)}")


async def list_downloads(session: AsyncSession) -> list[Download]:
    return list(
        (await session.scalars(select(Download).order_by(Download.product, Download.platform))).all()
    )


async def _row(session: AsyncSession, product: str, platform: str) -> Download | None:
    return await session.scalar(
        select(Download).where(Download.product == product, Download.platform == platform)
    )


async def metadata(session: AsyncSession, product: str, platform: str) -> Download:
    _validate(product, platform)
    row = await _row(session, product, platform)
    if row is None:
        raise NotFoundError("That download is not available yet")
    return row


async def fetch(
    session: AsyncSession, client: BaseClient, product: str, platform: str
) -> tuple[Download, garage.StoredFile]:
    row = await metadata(session, product, platform)
    stored = await garage.get_object(client, row.object_key)
    return row, stored


async def store(
    session: AsyncSession,
    client: BaseClient,
    product: str,
    platform: str,
    *,
    filename: str,
    content_type: str,
    version: str,
    content: bytes,
) -> Download:
    """Upload the binary to Garage and upsert its row. If the key changed, the old
    object is deleted after the new one is written."""
    _validate(product, platform)
    if not content:
        raise ValidationError("The uploaded file was empty")
    key = object_key(product, platform, filename)
    await garage.put_object(client, key, content, content_type or "application/octet-stream")

    row = await _row(session, product, platform)
    old_key = row.object_key if row else None
    if row is None:
        row = Download(product=product, platform=platform)
        session.add(row)
    row.filename = filename
    row.content_type = content_type or "application/octet-stream"
    row.size = len(content)
    row.version = version
    row.object_key = key
    await session.commit()
    await session.refresh(row)

    if old_key and old_key != key:
        await garage.delete_object(client, old_key)
    return row


async def delete_download(
    session: AsyncSession,
    client: BaseClient,
    product: str,
    platform: str,
) -> None:
    _validate(product, platform)
    row = await _row(session, product, platform)
    if row is None:
        raise NotFoundError("That download is not available")
    if row.object_key:
        try:
            await garage.delete_object(client, row.object_key)
        except Exception:
            pass
    await session.delete(row)
    await session.commit()

