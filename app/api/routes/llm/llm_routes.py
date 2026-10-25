"""Signed-in chat on the website. SSO cookie auth, no API key."""

from __future__ import annotations

from fastapi import APIRouter

import uuid

from app.api.deps import DbDep
from app.api.responses import job_response, present
from app.api.routes.llm import llm_service as service
from app.api.routes.llm.llm_schemas import ChatIn, FormatIn
from app.api.routes.model_links.model_links_schemas import LinkOut, LinkUpdate
from app.auth.sso import AdminDep, IdentityDep

router = APIRouter(prefix="/llm", tags=["llm"])


@router.get("/links", response_model=list[LinkOut])
async def links(user: AdminDep, db: DbDep):
    return await service.links(db, user.user_id)


@router.put("/links/{link_id}", response_model=LinkOut)
async def save_link(link_id: uuid.UUID, body: LinkUpdate, user: AdminDep, db: DbDep):
    return await service.save_link(db, user.user_id, link_id, body.url, body.enabled)


@router.post("/chat")
async def chat(body: ChatIn, user: IdentityDep, db: DbDep):
    return present(await service.chat(db, user.user_id, body.text))


@router.post("/format")
async def format_text(body: FormatIn, user: IdentityDep, db: DbDep):
    return present(await service.format_text(db, user.user_id, body.text, body.script))


@router.get("/jobs/{job_id}")
async def job(job_id: str, user: IdentityDep):
    body, status = await service.job(user.user_id, job_id)
    return job_response(body, status)
