"""Document Q&A on the website. Asking may be open to signed-out people (admin setting);
uploading always needs sign-in. Answers stream as NDJSON (see services/rag/answer.py)."""

from __future__ import annotations

import uuid
from typing import Any, Literal

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import StreamingResponse
from karan_sso.fastapi import OptionalIdentityDep
from pydantic import BaseModel, ConfigDict, Field

from app.api.deps import DbDep
from app.api.routes.rag import rag_service as service
from app.auth.sso import AdminDep, IdentityDep
from app.db.garage import garage_client, open_stream

router = APIRouter(prefix="/rag", tags=["rag"])

NDJSON_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


class Turn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=4000)


class AskIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=1000)
    target: Literal["book", "upload"] = "book"
    # One book on the shelf, or "all" / empty for every live book.
    book_id: str | None = Field(default=None, max_length=40)
    history: list[Turn] = Field(default_factory=list, max_length=8)


def maker(request: Request) -> Any:
    """A session factory for the stream (the request's own session closes before it ends).
    Resolved lazily, so errors raised before streaming never need the database."""

    def make():
        return request.app.state.postgres_session.sessionmaker()

    return make


@router.get("/status")
async def status(identity: OptionalIdentityDep, db: DbDep):
    return await service.status(db, identity)


@router.post("/ask")
async def ask(body: AskIn, request: Request, identity: OptionalIdentityDep, db: DbDep):
    stream = await service.ask(
        db,
        maker(request),
        identity,
        request,
        body.question,
        body.target,
        [turn.model_dump() for turn in body.history],
        body.book_id,
    )
    return StreamingResponse(stream, media_type="application/x-ndjson", headers=NDJSON_HEADERS)


@router.post("/upload")
async def upload(user: IdentityDep, db: DbDep, file: UploadFile = File(...)):
    return await service.upload(db, user, file)


@router.get("/upload")
async def my_upload(user: IdentityDep, db: DbDep):
    return {"upload": await service.my_upload(db, user)}


@router.delete("/upload", status_code=204)
async def delete_upload(user: IdentityDep, db: DbDep):
    await service.delete_upload(db, user)


@router.get("/media/{token}")
async def media(token: str, db: DbDep):
    key = await service.figure_stream(db, token)
    holder = garage_client()
    client = await holder.__aenter__()
    try:
        chunks, content_type, length = await open_stream(client, key)
    except BaseException:
        await holder.__aexit__(None, None, None)
        raise

    async def body():
        try:
            async for chunk in chunks:
                yield chunk
        finally:
            await holder.__aexit__(None, None, None)

    headers = {"Cache-Control": "private, max-age=600", "X-Content-Type-Options": "nosniff"}
    if length:
        headers["Content-Length"] = str(length)
    return StreamingResponse(body(), media_type=content_type, headers=headers)


# ---------------------------------------------------------------- admin

admin_router = APIRouter(prefix="/rag/admin", tags=["rag-admin"])


@admin_router.get("")
async def admin_view(user: AdminDep, db: DbDep):
    return await service.admin_view(db)


class RenameIn(BaseModel):
    title: str = Field(min_length=1, max_length=300)


@admin_router.post("/books")
async def admin_add_book(user: AdminDep, db: DbDep, file: UploadFile = File(...), title: str | None = Form(None)):
    return await service.admin_book_upload(db, file, title)


@admin_router.patch("/books/{source_id}")
async def admin_rename_book(source_id: uuid.UUID, body: RenameIn, user: AdminDep, db: DbDep):
    return await service.admin_rename_book(db, source_id, body.title)


@admin_router.delete("/books/{source_id}", status_code=204)
async def admin_delete_book(source_id: uuid.UUID, user: AdminDep, db: DbDep):
    await service.admin_delete(db, source_id, "book")


@admin_router.post("/books/{source_id}/retry")
async def admin_retry(source_id: uuid.UUID, user: AdminDep, db: DbDep):
    return await service.admin_retry(db, source_id)


@admin_router.delete("/uploads/{source_id}", status_code=204)
async def admin_delete_upload(source_id: uuid.UUID, user: AdminDep, db: DbDep):
    await service.admin_delete(db, source_id, "upload")
