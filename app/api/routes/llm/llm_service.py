"""Chat and cleanup use only the Oracle LLM link. Nothing is tried on the PC."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routes.llm.llm_constants import PROJECT, SLOTS, default_links
from app.api.routes.model_links import model_links_service
from app.api.routes.model_links.model import ProviderLink
from app.api.routes.services import service_switches
from app.api.routes.whisperflow import whisperflow_service as whisper
from app.services import gateway
from app.services.routing import LinkRow


def oracle_only(rows: list[LinkRow]) -> list[LinkRow]:
    return [row for row in rows if row.role == "llm" and row.placement == "remote"]


async def links(session: AsyncSession, owner_id: str) -> list[ProviderLink]:
    defaults = default_links()
    if not defaults.get(("llm", "remote"), "").strip():
        for row in await whisper.links(session, owner_id):
            if row.role == "llm" and row.placement == "remote" and (row.url or "").strip():
                defaults[("llm", "remote")] = row.url.strip()
                break
    return await model_links_service.ensure_links(session, owner_id, PROJECT, SLOTS, defaults)


async def save_link(
    session: AsyncSession, owner_id: str, link_id: uuid.UUID, url: str, enabled: bool
) -> ProviderLink:
    return await model_links_service.update_link(session, owner_id, PROJECT, link_id, url, enabled)


async def _rows(session: AsyncSession, owner_id: str) -> list[LinkRow]:
    saved = model_links_service.as_rows(await links(session, owner_id))
    return oracle_only(saved)


async def chat(session: AsyncSession, owner_id: str, text: str) -> dict:
    await service_switches.assert_open(session, PROJECT)
    cleaned = whisper.require_text(text)
    return await gateway.run_operation(await _rows(session, owner_id), "llm", "/chat", {"text": cleaned}, owner_id, PROJECT)


async def format_text(session: AsyncSession, owner_id: str, text: str, script: str) -> dict:
    await service_switches.assert_open(session, PROJECT)
    cleaned = whisper.require_text(text)
    chosen = "devanagari" if script.strip().lower() == "devanagari" else "roman"
    return await gateway.run_operation(
        await _rows(session, owner_id),
        "llm",
        "/format",
        {"text": cleaned, "script": chosen},
        owner_id,
        PROJECT,
    )


async def job(owner_id: str, job_id: str) -> tuple[dict, int]:
    return await gateway.read_job(job_id, owner_id)
