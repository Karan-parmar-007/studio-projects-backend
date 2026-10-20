"""Signed-in WhisperFlow: the playground on the website and the model-links page.
SSO cookie auth, no API key."""

from __future__ import annotations

import uuid

from fastapi import APIRouter

from app.api.deps import DbDep
from app.api.responses import job_response, present
from app.api.routes.model_links.model_links_schemas import LinkOut, LinkUpdate
from app.api.routes.whisperflow import whisperflow_service as service
from app.api.routes.whisperflow.whisperflow_schemas import FormatIn, TranscribeIn
from app.auth.sso import AdminDep, IdentityDep

router = APIRouter(prefix="/whisperflow", tags=["whisperflow"])


@router.get("/links", response_model=list[LinkOut])
async def links(user: AdminDep, db: DbDep):
    return await service.links(db, user.user_id)


@router.put("/links/{link_id}", response_model=LinkOut)
async def save_link(link_id: uuid.UUID, body: LinkUpdate, user: AdminDep, db: DbDep):
    return await service.save_link(db, user.user_id, link_id, body.url, body.enabled)


@router.post("/transcribe")
async def transcribe(body: TranscribeIn, user: IdentityDep, db: DbDep):
    audio = service.require_audio(body.audio_base64)
    return present(await service.run(db, user.user_id, "transcribe", {"audio_base64": audio}))


@router.post("/format")
async def format_text(body: FormatIn, user: IdentityDep, db: DbDep):
    text = service.require_text(body.text)
    payload = {"text": text, "tone": body.tone, "script": body.script}
    return present(await service.run(db, user.user_id, "format", payload))


@router.get("/jobs/{job_id}")
async def job(job_id: str, user: IdentityDep):
    body, status = await service.job(user.user_id, job_id)
    return job_response(body, status)
