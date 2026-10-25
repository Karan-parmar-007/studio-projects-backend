from __future__ import annotations

import base64
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routes.model_links import model_links_service
from app.api.routes.services import service_switches
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


MAX_AUDIO_BYTES = 2 * 1024 * 1024


def require_audio(audio_base64: str) -> str:
    raw = audio_base64.strip()
    if not raw:
        raise ValidationError("audio_base64 is required")
    try:
        size = len(base64.b64decode(raw, validate=False))
    except Exception as exc:
        raise ValidationError("audio_base64 is not valid") from exc
    if size > MAX_AUDIO_BYTES:
        raise ValidationError("Audio files must be 2 MB or smaller")
    return raw


async def run(session: AsyncSession, owner_id: str, operation: str, payload: dict) -> dict:
    await service_switches.assert_open(session, PROJECT)
    role, path = OPERATIONS[operation]
    rows = model_links_service.as_rows(await links(session, owner_id))
    return await gateway.run_operation(rows, role, path, payload, owner_id, PROJECT)


async def job(owner_id: str, job_id: str) -> tuple[dict, int]:
    return await gateway.read_job(job_id, owner_id)
