"""The site assistant (Studio + portfolio). Signed-out use depends on the admin's access
setting; tools always need sign-in. Replies stream as NDJSON."""

from __future__ import annotations

import tempfile
import uuid
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, File, Query, Request, UploadFile
from fastapi.responses import StreamingResponse
from karan_sso.fastapi import OptionalIdentityDep
from pydantic import BaseModel, ConfigDict, Field

from app.api.deps import DbDep
from app.api.routes.chatbot import chatbot_service as service
from app.api.routes.rag import rag_service
from app.auth.sso import AdminDep
from app.core.errors import ConflictError
from app.db.garage import copy_object, garage_client, put_object
from app.services.rag import jobs, media
from app.services.rag import sources as source_lib
from app.services.rag.model import RagSource

router = APIRouter(prefix="/chatbot", tags=["chatbot"])

NDJSON_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
KNOWLEDGE_MAX_BYTES = 50 * 1024 * 1024


class Turn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=4000)


class ChatIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1, max_length=1000)
    history: list[Turn] = Field(default_factory=list, max_length=12)
    surface: Literal["studio", "portfolio"] = "studio"


def maker(request: Request) -> Any:
    """A session factory for the stream (the request's own session closes before it ends).
    Resolved lazily, so errors raised before streaming never need the database."""

    def make():
        return request.app.state.postgres_session.sessionmaker()

    return make


@router.get("/config")
async def config(
    identity: OptionalIdentityDep, db: DbDep, surface: Literal["studio", "portfolio"] = Query("studio")
):
    return await service.config(db, identity, surface)


@router.post("/chat")
async def chat(body: ChatIn, request: Request, identity: OptionalIdentityDep, db: DbDep):
    stream = await service.chat(
        db,
        maker(request),
        identity,
        request,
        body.message,
        [turn.model_dump() for turn in body.history],
        body.surface,
    )
    return StreamingResponse(stream, media_type="application/x-ndjson", headers=NDJSON_HEADERS)


# ---------------------------------------------------------------- admin

admin_router = APIRouter(prefix="/chatbot/admin", tags=["chatbot-admin"])


@admin_router.get("")
async def admin_view(user: AdminDep, db: DbDep):
    return await service.admin_view(db)


async def _new_version(db, *, filename: str, size: int, input_key: str | None, source_id: uuid.UUID) -> dict:
    source = RagSource(
        id=source_id,
        scope="chatbot",
        title="Karan Parmar",
        filename=filename,
        content_type="application/pdf" if filename.lower().endswith(".pdf") else "",
        size_bytes=size,
        input_key=input_key,
    )
    db.add(source)
    await db.flush()
    await jobs.enqueue(db, "ingest", source_id, lane="slow", priority=5, commit=False)
    await db.commit()
    return rag_service.source_view(source) or {}


@admin_router.post("/knowledge")
async def upload_knowledge(user: AdminDep, db: DbDep, file: UploadFile = File(...)):
    """A new knowledge PDF (or DOCX/MD). Built beside the live corpus with the portfolio, then
    swapped in; the old version and its files are deleted afterwards."""
    if await source_lib.building(db, "chatbot") is not None:
        raise ConflictError("A new version is already being built. Wait for it to finish.")
    filename = Path(file.filename or "knowledge.pdf").name[:300]
    ext = rag_service._check_type(filename)
    source_id = uuid.uuid4()
    with tempfile.TemporaryDirectory(prefix="chatbot-") as folder:
        path, size = await rag_service._save_upload(file, KNOWLEDGE_MAX_BYTES, folder)
        key = media.input_key(source_id, filename)
        async with garage_client() as client:
            with open(path, "rb") as handle:
                await put_object(client, key, handle.read(), rag_service.CONTENT_TYPES.get(ext, "application/octet-stream"))
    return await _new_version(db, filename=filename, size=size, input_key=key, source_id=source_id)


@admin_router.post("/sync")
async def sync(user: AdminDep, db: DbDep):
    """Rebuild from the current knowledge file and fresh portfolio data (after editing the portfolio)."""
    if await source_lib.building(db, "chatbot") is not None:
        raise ConflictError("A new version is already being built. Wait for it to finish.")
    current = await source_lib.active(db, "chatbot")
    source_id = uuid.uuid4()
    key: str | None = None
    filename, size = "", 0
    if current is not None and current.input_key:
        # The new version gets its own copy: deleting the old version deletes its prefix.
        filename, size = current.filename, current.size_bytes
        key = media.input_key(source_id, filename)
        async with garage_client() as client:
            await copy_object(client, current.input_key, key)
    # No file yet: the first build uses the portfolio and the Studio catalog only.
    return await _new_version(db, filename=filename, size=size, input_key=key, source_id=source_id)
