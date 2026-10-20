"""WhisperFlow for the desktop app, the Android keyboard, and anyone with a key.
Authorization: Bearer sk-studio_... (the user's one key)."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import DbDep
from app.api.responses import job_response, present
from app.api.routes.api_key.api_key_auth import ApiKeyDep
from app.api.routes.whisperflow import whisperflow_service as service
from app.api.routes.whisperflow.whisperflow_schemas import FormatIn, TranscribeIn

router = APIRouter(prefix="/v1/whisperflow", tags=["whisperflow-public"])


@router.post("/transcribe")
async def transcribe(body: TranscribeIn, key: ApiKeyDep, db: DbDep):
    audio = service.require_audio(body.audio_base64)
    return present(await service.run(db, key.owner_id, "transcribe", {"audio_base64": audio}))


@router.post("/format")
async def format_text(body: FormatIn, key: ApiKeyDep, db: DbDep):
    text = service.require_text(body.text)
    payload = {"text": text, "tone": body.tone, "script": body.script}
    return present(await service.run(db, key.owner_id, "format", payload))


@router.get("/jobs/{job_id}")
async def job(job_id: str, key: ApiKeyDep):
    body, status = await service.job(key.owner_id, job_id)
    return job_response(body, status)
