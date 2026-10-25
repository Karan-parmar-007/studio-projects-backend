"""LLM chat for anyone with a Studio API key."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import DbDep
from app.api.responses import job_response, present
from app.api.routes.api_key.api_key_auth import ApiKeyDep
from app.api.routes.llm import llm_service as service
from app.api.routes.llm.llm_schemas import ChatIn, FormatIn

router = APIRouter(prefix="/v1/llm", tags=["llm-public"])


@router.post("/chat")
async def chat(body: ChatIn, key: ApiKeyDep, db: DbDep):
    return present(await service.chat(db, key.owner_id, body.text))


@router.post("/format")
async def format_text(body: FormatIn, key: ApiKeyDep, db: DbDep):
    return present(await service.format_text(db, key.owner_id, body.text, body.script))


@router.get("/jobs/{job_id}")
async def job(job_id: str, key: ApiKeyDep):
    body, status = await service.job(key.owner_id, job_id)
    return job_response(body, status)
