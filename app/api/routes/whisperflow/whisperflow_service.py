from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routes.model_links import model_links_service
from app.api.routes.model_links.model import ProviderLink
from app.api.routes.whisperflow.whisperflow_constants import OPERATIONS, PROJECT, SLOTS, default_links
from app.core.errors import ValidationError
from app.services import gateway


async def links(session: AsyncSession, owner_id: str) -> list[ProviderLink]:
    return await model_links_service.ensure_links(session, owner_id, PROJECT, SLOTS, default_links())


async def save_link(
    session: AsyncSession, owner_id: str, link_id: uuid.UUID, url: str, enabled: bool
) -> ProviderLink:
    return await model_links_service.update_link(session, owner_id, PROJECT, link_id, url, enabled)


def require_text(text: str) -> str:
    cleaned = text.strip()
    if not cleaned:
        raise ValidationError("Text is required")
    if len(cleaned) > 20000:
        raise ValidationError("Text is too long for one request")
    return cleaned


def require_audio(audio_base64: str) -> str:
    if not audio_base64.strip():
        raise ValidationError("audio_base64 is required")
    return audio_base64


async def run(session: AsyncSession, owner_id: str, operation: str, payload: dict) -> dict:
    role, path = OPERATIONS[operation]
    rows = model_links_service.as_rows(await links(session, owner_id))
    return await gateway.run_operation(rows, role, path, payload, owner_id, PROJECT)


async def job(owner_id: str, job_id: str) -> tuple[dict, int]:
    return await gateway.read_job(job_id, owner_id)
