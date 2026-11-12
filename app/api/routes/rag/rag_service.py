"""Document Q&A: the default book for everyone allowed, plus one personal upload per user.

Uploads: up to 5 MB, one at a time (delete the old one to upload a new one), kept
for an hour after processing finishes, then the sweeper deletes the file, its
vectors and its images.
"""

from __future__ import annotations

import logging
import os
import tempfile
import time
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from fastapi import Request, UploadFile
from karan_sso import SsoIdentity
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routes.services import service_switches
from app.api.routes.settings import settings_service as access
from app.config import garage_settings
from app.core.errors import ConflictError, ModelOfflineError, NotFoundError, QuotaError, ValidationError
from app.db.garage import delete_prefix, garage_client, put_object
from app.services import llm_client
from app.services.rag import answer as answers
from app.services.rag import jobs, media
from app.services.rag import sources as source_lib
from app.services.rag.model import RagSource
from app.services.rag.parse import SUPPORTED_EXTENSIONS
from app.services.rag.retrieve import retrieve

logger = logging.getLogger(__name__)

PROJECT = "rag"
UPLOAD_CHUNK = 1 << 16
BOOK_MAX_BYTES = 1024 * 1024 * 1024  # 1 GB

CONTENT_TYPES = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".html": "text/html",
    ".htm": "text/html",
}


def source_view(source: RagSource | None) -> dict | None:
    if source is None:
        return None
    return {
        "id": str(source.id),
        "title": source.title or Path(source.filename).stem,
        "filename": source.filename,
        "size_bytes": source.size_bytes,
        "status": source.status,
        "stage": source.stage,
        "progress": source.progress,
        "error": source.error,
        "active": source.active,
        "pages": source.page_count,
        "chunks": source.chunk_count,
        "figures": source.figure_count,
        "created_at": source.created_at,
        "ready_at": source.ready_at,
        "completed_at": source.completed_at,
        "expires_at": source.expires_at,
    }


def usable(source: RagSource | None) -> bool:
    if source is None or source.status != "ready":
        return False
    if source.expires_at is not None and source.expires_at <= source_lib.now():
        return False
    return True


async def status(session: AsyncSession, identity: SsoIdentity | None) -> dict:
    settings = await access.load(session, PROJECT)
    enabled = await service_switches.is_enabled(session, PROJECT)
    allowed = True
    reason = ""
    try:
        access.check_access(settings, identity)
    except Exception as exc:  # noqa: BLE001
        allowed = False
        reason = getattr(exc, "message", "")
    books = await source_lib.books(session) if isinstance(session, AsyncSession) else []
    upload = (
        await source_lib.upload_of(session, identity.user_id)
        if identity is not None and isinstance(session, AsyncSession)
        else None
    )
    return {
        "enabled": enabled,
        "access": settings.access,
        "signed_in": identity is not None,
        "can_use": enabled and allowed,
        "reason": reason,
        "uploads_enabled": settings.uploads_enabled,
        "upload_max_mb": settings.upload_max_mb,
        "upload_ttl_minutes": settings.upload_ttl_minutes,
        "upload_max_pages": settings.upload_max_pages,
        "accepted": sorted(SUPPORTED_EXTENSIONS),
        "books": [source_view(b) for b in books],
        "book": source_view(books[0]) if books else None,
        "upload": source_view(upload),
    }


async def _save_upload(file: UploadFile, max_bytes: int, folder: str) -> tuple[str, int]:
    name = Path(file.filename or "document").name or "document"
    path = os.path.join(folder, name)
    size = 0
    with open(path, "wb") as handle:
        while True:
            chunk = await file.read(UPLOAD_CHUNK)
            if not chunk:
                break
            size += len(chunk)
            if size > max_bytes:
                raise ValidationError(f"That file is larger than {max_bytes // (1024 * 1024)} MB.")
            handle.write(chunk)
    if size == 0:
        raise ValidationError("That file is empty.")
    return path, size


def _check_type(filename: str) -> str:
    ext = Path(filename).suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise ValidationError("Upload a PDF, Word, PowerPoint, Markdown, HTML or text file.")
    return ext


async def upload(session: AsyncSession, identity: SsoIdentity, file: UploadFile) -> dict:
    await service_switches.assert_open(session, PROJECT)
    settings = await access.load(session, PROJECT)
    access.check_access(settings, identity)
    if not settings.uploads_enabled:
        raise ValidationError("Uploading your own document is switched off right now.")
    filename = Path(file.filename or "document").name[:300]
    ext = _check_type(filename)
    if await source_lib.upload_of(session, identity.user_id) is not None:
        raise ConflictError("Delete your current document before uploading a new one.")
    source_id = uuid.uuid4()
    with tempfile.TemporaryDirectory(prefix="rag-up-") as folder:
        path, size = await _save_upload(file, settings.upload_max_mb * 1024 * 1024, folder)
        key = media.input_key(source_id, filename)
        async with garage_client() as client:
            with open(path, "rb") as handle:
                await put_object(client, key, handle.read(), CONTENT_TYPES.get(ext, "application/octet-stream"))
    source = RagSource(
        id=source_id,
        scope="upload",
        owner_id=identity.user_id,
        owner_label=(identity.email or identity.name or "")[:200],
        title=Path(filename).stem[:300],
        filename=filename,
        content_type=CONTENT_TYPES.get(ext, ""),
        size_bytes=size,
        input_key=key,
    )
    session.add(source)
    try:
        await session.flush()
    except Exception as exc:  # unique: one upload per user (a race with a second tab)
        await session.rollback()
        async with garage_client() as client:
            await delete_prefix(client, media.storage_prefix(source_id))
        raise ConflictError("Delete your current document before uploading a new one.") from exc
    await jobs.enqueue(session, "ingest", source_id, lane="fast", priority=0, commit=False)
    await session.commit()
    return source_view(source) or {}


async def my_upload(session: AsyncSession, identity: SsoIdentity) -> dict | None:
    return source_view(await source_lib.upload_of(session, identity.user_id))


async def delete_upload(session: AsyncSession, identity: SsoIdentity) -> None:
    source = await source_lib.upload_of(session, identity.user_id)
    if source is None:
        raise NotFoundError("You have no document uploaded")
    await source_lib.delete_source(session, source.id)


# ---------------------------------------------------------------- asking


async def _target(session: AsyncSession, identity: SsoIdentity | None, target: str) -> RagSource:
    if target == "upload":
        if identity is None:
            raise ValidationError("Sign in to ask about your own document.")
        source = await source_lib.upload_of(session, identity.user_id)
        if source is None:
            raise NotFoundError("Upload a document first.")
        if not usable(source):
            if source.status in ("queued", "processing"):
                raise ConflictError("Your document is still being read. It will be ready in a moment.")
            raise NotFoundError("Your document is no longer available. Upload it again.")
        return source
    raise ValidationError("Unknown target")


async def _targets(
    session: AsyncSession, identity: SsoIdentity | None, target: str, book_id: str | None
) -> list[RagSource]:
    if target == "upload":
        return [await _target(session, identity, target)]
    shelf = await source_lib.books(session)
    if not shelf:
        raise NotFoundError("No book is ready yet.")
    if book_id and book_id != "all":
        chosen = [book for book in shelf if str(book.id) == book_id]
        if not chosen:
            raise NotFoundError("That book is not available.")
        return chosen
    return shelf


async def ask(
    session: AsyncSession,
    maker: Any,
    identity: SsoIdentity | None,
    request: Request,
    question: str,
    target: str,
    history: list[dict],
    book_id: str | None = None,
) -> AsyncIterator[str]:
    await service_switches.assert_open(session, PROJECT)
    settings = await access.load(session, PROJECT)
    access.check_access(settings, identity)
    question = question.strip()
    if not question:
        raise ValidationError("Ask a question.")
    targets = await _targets(session, identity, target, book_id)
    subject = access.subject_for(identity, request)
    access.in_flight.claim(f"{PROJECT}:{subject}")
    try:
        limit = settings.limits.daily_signed_in if identity else settings.limits.daily_anonymous
        await access.take_daily(session, PROJECT, subject, limit)
        try:
            ahead = await access.check_line("rag", settings.limits, identity is not None)
        except QuotaError:
            await access.give_back(session, PROJECT, subject)
            raise
    except BaseException:
        access.in_flight.release(f"{PROJECT}:{subject}")
        raise
    return _answer_stream(maker, targets, question, history, subject, ahead)


async def _answer_stream(
    maker: Any, targets: RagSource | list[RagSource], question: str, history: list[dict], subject: str, ahead: int
) -> AsyncIterator[str]:
    targets = targets if isinstance(targets, list) else [targets]
    source = targets[0]
    key = f"{PROJECT}:{subject}"
    answered = False
    try:
        async with maker() as session:
            yield answers.event("status", stage="searching", ahead=ahead)
            query = answers.search_query(question, history)
            vector = (await llm_client.embed([query], query=True))[0]
            # One book, first question: shared answers. Several books or a conversation: never.
            cacheable = source.scope == "book" and len(targets) == 1 and not history
            if cacheable:
                hit = await answers.cached_answer(session, source.id, question, vector)
                if hit is not None:
                    for line in answers.replay(hit):
                        yield line
                    answered = True
                    return
            found = await retrieve(
                session,
                [t.id for t in targets],
                query,
                total_chunks=sum(t.chunk_count for t in targets),
                query_vector=vector,
                scope=source.scope,
            )
            titles = {t.id: t.title or t.filename for t in targets}
            if not found.passages or not found.confident:
                yield answers.event("sources", sources=[], figures=[])
                yield answers.event("token", text=answers.NOT_FOUND)
                yield answers.event("done", cached=False, found=False)
                answered = True
                return
            sources = answers.source_list(found.passages, titles)
            figures = answers.figure_list(found.figures)
            started = time.monotonic()
            yield await answers.waiting_status(started)
            model = await llm_client.chat_model("rag", temperature=0.3)
            text_out = ""
            # Hold the first characters back: the model answers NOT_FOUND when the passages do
            # not cover the question, and then no unrelated sources or figures should show.
            held = True
            try:
                async for chunk in answers.with_heartbeat(model.astream(answers.rag_messages(found, question, history))):
                    if chunk is answers.HEARTBEAT:
                        if held:
                            yield await answers.waiting_status(started)
                        continue
                    piece = chunk.content if isinstance(chunk.content, str) else ""
                    if not piece:
                        continue
                    text_out += piece
                    if held:
                        sentence = answers.first_sentence(text_out)
                        if sentence is None:
                            continue  # wait for the whole first sentence
                        if answers.is_refusal(sentence):
                            # keep reading a little: "...do not contain X. However, ..." is partial
                            if len(text_out) < len(sentence) + 120:
                                continue
                            if answers.is_refusal(text_out):
                                break
                        held = False
                        yield answers.event("sources", sources=sources, figures=figures)
                        answered = True
                        yield answers.event("token", text=text_out)
                        continue
                    yield answers.event("token", text=piece)
            except Exception as exc:  # noqa: BLE001
                raise llm_client.gateway_error(exc) from exc
            if held or answers.is_refusal(text_out):  # NOT_FOUND, or refused in words
                refused = answers.is_refusal(text_out)
                yield answers.event("sources", sources=[] if refused else sources, figures=[] if refused else figures)
                yield answers.event("token", text=answers.NOT_FOUND if refused else text_out)
                yield answers.event("done", cached=False, found=not refused)
                answered = True
                return
            # Trim the list to what the answer actually cites (the UI replaces it on this event).
            sources, figures = answers.keep_cited(text_out, sources, figures)
            yield answers.event("sources", sources=sources, figures=figures)
            yield answers.event("done", cached=False, found=True)
            if cacheable and text_out.strip():
                await answers.store_answer(
                    session,
                    source.id,
                    question,
                    vector,
                    {"text": text_out, "sources": sources, "figures": figures, "found": True},
                )
    except (QuotaError, ModelOfflineError) as exc:
        yield answers.event("error", message=exc.message, code=getattr(exc, "code", "model_offline"))
    except Exception:  # noqa: BLE001
        logger.exception("rag answer failed")
        yield answers.event("error", message="Something went wrong. Try again.", code="error")
    finally:
        access.in_flight.release(key)
        if not answered:
            try:
                async with maker() as session:
                    await access.give_back(session, PROJECT, subject)
            except Exception:  # noqa: BLE001
                logger.exception("could not give back a question")


# ---------------------------------------------------------------- media


async def figure_stream(session: AsyncSession, token: str):
    figure_id = media.verify(token)
    if figure_id is None:
        raise NotFoundError("That link has expired")
    row = (
        await session.execute(
            text(
                """
                SELECT f.storage_key FROM studio.rag_figures f
                JOIN studio.rag_sources s ON s.id = f.source_id
                WHERE f.id = :id AND (s.expires_at IS NULL OR s.expires_at > now())
                """
            ),
            {"id": figure_id},
        )
    ).first()
    if row is None:
        raise NotFoundError("That image is no longer available")
    return row[0]


# ---------------------------------------------------------------- admin


MAX_BOOKS = int(os.environ.get("RAG_MAX_BOOKS", "5"))


def admin_source_view(source: RagSource) -> dict:
    view = source_view(source) or {}
    view.update({"owner_id": source.owner_id, "owner_label": source.owner_label, "scope": source.scope})
    return view


async def admin_view(session: AsyncSession) -> dict:
    """Settings, every book (any status), and every user upload present right now."""
    settings = await access.load(session, PROJECT)
    shelf = await source_lib.books(session, live_only=False)
    uploads = await source_lib.uploads(session)
    return {
        "settings": settings.model_dump(),
        "enabled": await service_switches.is_enabled(session, PROJECT),
        "books": [admin_source_view(b) for b in shelf],
        "uploads": [admin_source_view(u) for u in uploads],
        "max_books": MAX_BOOKS,
    }


async def admin_book_upload(session: AsyncSession, file: UploadFile, title: str | None = None) -> dict:
    """Add a book to the shelf. It goes live on its own when processing finishes."""
    filename = Path(file.filename or "book").name[:300]
    ext = _check_type(filename)
    if len(await source_lib.books(session, live_only=False)) >= MAX_BOOKS:
        raise ConflictError(f"The shelf holds {MAX_BOOKS} books. Delete one first.")
    source_id = uuid.uuid4()
    with tempfile.TemporaryDirectory(prefix="rag-book-") as folder:
        path, size = await _save_upload(file, BOOK_MAX_BYTES, folder)
        key = media.input_key(source_id, filename)
        async with garage_client() as client:
            with open(path, "rb") as handle:
                await client.upload_fileobj(
                    handle,
                    garage_settings.GARAGE_BUCKET_NAME,
                    key,
                    ExtraArgs={"ContentType": CONTENT_TYPES.get(ext, "application/octet-stream")},
                )
    clean_title = (title or "").strip()[:300]
    source = RagSource(
        id=source_id,
        scope="book",
        title=clean_title or Path(filename).stem[:300],
        filename=filename,
        content_type=CONTENT_TYPES.get(ext, ""),
        size_bytes=size,
        input_key=key,
    )
    session.add(source)
    await session.flush()
    await jobs.enqueue(session, "ingest", source_id, lane="slow", priority=9, commit=False)
    await session.commit()
    return admin_source_view(source)


async def admin_rename_book(session: AsyncSession, source_id: uuid.UUID, title: str) -> dict:
    source = await session.get(RagSource, source_id)
    if source is None or source.scope != "book":
        raise NotFoundError("Unknown book")
    clean = title.strip()[:300]
    if not clean:
        raise ValidationError("Give the book a title.")
    source.title = clean
    await session.commit()
    return admin_source_view(source)


async def admin_retry(session: AsyncSession, source_id: uuid.UUID) -> dict:
    source = await session.get(RagSource, source_id)
    if source is None or source.scope not in source_lib.PUBLIC_SCOPES:
        raise NotFoundError("Unknown version")
    if source.status != "failed":
        raise ConflictError("Only a failed version can be retried.")
    source.status = "queued"
    source.stage = "queued"
    source.error = None
    await jobs.enqueue(session, "ingest", source.id, lane="slow", priority=9 if source.scope == "book" else 5, commit=False)
    await session.commit()
    return admin_source_view(source)


async def admin_delete(session: AsyncSession, source_id: uuid.UUID, scope: str) -> None:
    """Delete a book or a user's upload completely: its images and original file in Garage,
    then its rows (chunks with their vectors, figures, entities, jobs, cached answers).
    A document still being processed stops at its next step and removes what it stored."""
    source = await session.get(RagSource, source_id)
    if source is None or source.scope != scope:
        raise NotFoundError("Already deleted")
    await source_lib.delete_source(session, source_id)
