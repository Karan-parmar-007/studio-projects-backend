"""The document pipeline studio-worker runs for one source version.

  parse (Docling, page batches)  -> figures to Garage as WebP
  chunk                          -> rows, no vectors yet
  embed                          -> chunk vectors ("Document / Section / text")
  link                           -> entities (GLiNER2) + chunk<->entity rows
  graph                          -> graph-smoothed vectors
  READY (searchable)             -> chatbot/book: switch live version, delete the old one
  describe figures               -> vision model on the ingest lane, re-embed figure chunks
  COMPLETE                       -> uploads: expires_at = now + TTL

A failure marks the source failed with a readable message; the job layer retries.
"""

from __future__ import annotations

import asyncio
import io
import logging
import os
import tempfile
import uuid
from collections import Counter
from datetime import timedelta
from pathlib import Path

import numpy as np
from sqlalchemy import select, text

from app.config import rag_settings
from app.db.garage import delete_prefix, download_to_file, garage_client, get_object, put_object
from app.services import llm_client
from app.services.rag import entities as entity_lib
from app.services.rag import media, portfolio
from app.services.rag import sources as source_lib
from app.services.rag.chunk import ChunkDraft, embed_text, figure_ref, figure_text, make_chunks
from app.services.rag.graph import ChunkMeta, build
from app.services.rag.model import RagFigure, RagSource
from app.services.rag.parse import (
    Block,
    ParseError,
    ParsedDoc,
    iter_parts,
    pdf_page_count,
    release_models,
    webp_bytes,
)
from app.services.rag.retrieve import vector_literal

logger = logging.getLogger(__name__)

UPLOAD_FIGURE_LIMIT = 12
EMBED_GROUP = 64


class SourceGone(Exception):
    """The admin (or the user) deleted the document while it was being processed."""


async def _ensure_exists(maker, source_id: uuid.UUID) -> None:
    async with maker() as session:
        if not await source_lib.exists(session, source_id):
            raise SourceGone(str(source_id))


class IngestError(Exception):
    """A message the user may read."""


async def _source(session, source_id: uuid.UUID) -> RagSource | None:
    return await session.scalar(select(RagSource).where(RagSource.id == source_id))


async def run(maker, source_id: uuid.UUID) -> None:
    async with maker() as session:
        source = await _source(session, source_id)
        if source is None:
            return
        if source.status == "ready" and source.completed_at is not None:
            return  # already done (a retried job)
        await source_lib.set_stage(session, source_id, "parsing", 2, status="processing", error=None)
    try:
        await _pipeline(maker, source_id)
    except SourceGone:
        # Deleted mid-way: remove anything this run stored after the delete, then stop quietly.
        await _remove_leftovers(source_id)
        return
    except (IngestError, ParseError) as exc:
        async with maker() as session:
            await source_lib.set_stage(session, source_id, "failed", 0, status="failed", error=str(exc)[:500])
        return
    except Exception:
        async with maker() as session:
            if not await source_lib.exists(session, source_id):
                await _remove_leftovers(source_id)
                return
            await source_lib.set_stage(
                session, source_id, "failed", 0, status="failed",
                error="Something went wrong while reading this document.",
            )
        raise


async def _remove_leftovers(source_id: uuid.UUID) -> None:
    try:
        async with garage_client() as client:
            await delete_prefix(client, media.storage_prefix(source_id))
    except Exception:  # noqa: BLE001
        logger.exception("could not remove leftover files of deleted source %s", source_id)


async def _clear_partial(session, source_id: uuid.UUID) -> None:
    """A retry starts clean: drop rows a previous attempt left behind."""
    await session.execute(text("DELETE FROM studio.rag_chunks WHERE source_id = :s"), {"s": source_id})
    await session.execute(text("DELETE FROM studio.rag_figures WHERE source_id = :s"), {"s": source_id})
    await session.execute(text("DELETE FROM studio.rag_entities WHERE source_id = :s"), {"s": source_id})
    await session.commit()


async def _store_figures(client, source_id: uuid.UUID, chunks: list[ChunkDraft]) -> list[dict]:
    rows = []
    for chunk in chunks:
        figure = chunk.figure
        if figure is None or not figure.image_png:
            continue
        data, width, height = await asyncio.to_thread(webp_bytes, figure.image_png)
        ext, mime = ("webp", "image/webp") if width else ("png", "image/png")
        key = media.figure_key(source_id, figure.number, ext)
        await put_object(client, key, data, mime)
        figure_id = uuid.uuid4()
        rows.append(
            {
                "id": figure_id,
                "source_id": source_id,
                "number": figure.number,
                "ref": figure_ref(figure)[:40],
                "label": figure.label,
                "page": figure.page,
                "caption": figure.caption,
                "description": "",
                "storage_key": key,
                "width": width or figure.width,
                "height": height or figure.height,
                "chunk_ord": chunk.ord,
            }
        )
        chunk.figure_id = figure_id  # type: ignore[attr-defined]
        figure.image_png = b""  # free memory; the bytes live in Garage now
    return rows


async def _insert_chunks(session, source_id: uuid.UUID, chunks: list[ChunkDraft], figures: list[dict]) -> None:
    if figures:
        await session.execute(
            text(
                """
                INSERT INTO studio.rag_figures (id, source_id, number, ref, label, page, caption, description,
                                                storage_key, width, height, chunk_ord)
                VALUES (:id, :source_id, :number, :ref, :label, :page, :caption, :description,
                        :storage_key, :width, :height, :chunk_ord)
                """
            ),
            figures,
        )
    if chunks:
        await session.execute(
            text(
                """
                INSERT INTO studio.rag_chunks (source_id, ord, kind, heading, content, page, tokens, figure_id, ref_ords)
                VALUES (:source_id, :ord, :kind, :heading, :content, :page, :tokens, :figure_id, :ref_ords)
                """
            ),
            [
                {
                    "source_id": source_id,
                    "ord": c.ord,
                    "kind": c.kind,
                    "heading": c.heading,
                    "content": c.content,
                    "page": c.page,
                    "tokens": c.tokens,
                    "figure_id": getattr(c, "figure_id", None),
                    "ref_ords": list(c.ref_ords),
                }
                for c in chunks
            ],
        )
    await session.commit()


def _renumber(chunks: list[ChunkDraft], offset: int) -> list[ChunkDraft]:
    if offset:
        for chunk in chunks:
            chunk.ord += offset
            chunk.ref_ords = [ref + offset for ref in chunk.ref_ords]
    return chunks


async def _parse_into_db(maker, source: RagSource, path: str | None, extra_blocks: list[Block]) -> tuple[str, int, int]:
    """Parse, store figures and chunk rows batch by batch. Returns (title, pages, chunk count)."""
    title = source.title
    pages = 0
    count = 0
    async with garage_client() as client:

        async def take(parsed: ParsedDoc, done: int, total: int) -> None:
            nonlocal title, pages, count
            title = title or parsed.title
            pages = total
            await _ensure_exists(maker, source.id)
            chunks = _renumber(make_chunks(parsed.blocks), count)
            figures = await _store_figures(client, source.id, chunks)
            async with maker() as session:
                await _insert_chunks(session, source.id, chunks, figures)
                await source_lib.set_stage(session, source.id, "parsing", 2 + int(28 * done / max(total, 1)))
            count += len(chunks)

        if extra_blocks:
            await take(ParsedDoc(title=title, blocks=extra_blocks), 0, 1)
        if path:
            parts = iter_parts(path, source.filename)
            while True:
                part = await asyncio.to_thread(next, parts, None)
                if part is None:
                    break
                await take(*part)
    return title, pages, count


async def _chunk_rows(session, source_id: uuid.UUID) -> list[tuple]:
    rows = await session.execute(
        text(
            "SELECT id, ord, kind, heading, content, ref_ords FROM studio.rag_chunks "
            "WHERE source_id = :s ORDER BY ord"
        ),
        {"s": source_id},
    )
    return [tuple(r) for r in rows]


async def _embed_all(maker, source: RagSource, title: str, rows: list[tuple], lane: str) -> np.ndarray:
    vectors = np.zeros((len(rows), rag_settings.RAG_EMBED_DIM), dtype=np.float32)
    for start in range(0, len(rows), EMBED_GROUP):
        await _ensure_exists(maker, source.id)
        group = rows[start : start + EMBED_GROUP]
        embedded = await llm_client.embed([embed_text(title, r[3], r[4]) for r in group], lane=lane)
        vectors[start : start + len(group)] = np.asarray(embedded, dtype=np.float32)
        async with maker() as session:
            await session.execute(
                text("UPDATE studio.rag_chunks SET embedding = CAST(:v AS halfvec) WHERE id = :id"),
                [{"id": r[0], "v": vector_literal(v)} for r, v in zip(group, embedded)],
            )
            await source_lib.set_stage(session, source.id, "embedding", 30 + int(45 * (start + len(group)) / len(rows)))
    return vectors


async def _link_entities(maker, source_id: uuid.UUID, rows: list[tuple]) -> list[tuple[int, int, int]]:
    found = await asyncio.to_thread(entity_lib.extract, [f"{r[3]}\n{r[4]}" for r in rows])
    df: Counter[str] = Counter()
    names: dict[str, tuple[str, str]] = {}
    per_chunk: list[set[str]] = []
    for pairs in found:
        keys = set()
        for name, label in pairs:
            key = entity_lib.norm(name)
            if key:
                keys.add(key)
                names.setdefault(key, (name, label))
        per_chunk.append(keys)
        df.update(keys)
    if not names:
        return []
    async with maker() as session:
        await session.execute(
            text(
                """
                INSERT INTO studio.rag_entities (source_id, norm, name, label, df)
                VALUES (:s, :norm, :name, :label, :df)
                ON CONFLICT (source_id, norm) DO UPDATE SET df = EXCLUDED.df
                """
            ),
            [{"s": source_id, "norm": k, "name": v[0], "label": v[1], "df": df[k]} for k, v in names.items()],
        )
        ids = {
            row[1]: int(row[0])
            for row in await session.execute(
                text("SELECT id, norm FROM studio.rag_entities WHERE source_id = :s"), {"s": source_id}
            )
        }
        links = [
            {"c": int(row[0]), "e": ids[key]}
            for row, keys in zip(rows, per_chunk)
            for key in keys
            if key in ids
        ]
        if links:
            await session.execute(
                text("INSERT INTO studio.rag_chunk_entities (chunk_id, entity_id) VALUES (:c, :e) ON CONFLICT DO NOTHING"),
                links,
            )
        await session.commit()
        return [(link["c"], link["e"], df[key]) for link, key in _keys_of(links, ids)]


def _keys_of(links: list[dict], ids: dict[str, int]):
    by_id = {value: key for key, value in ids.items()}
    for link in links:
        yield link, by_id[link["e"]]


async def _graph_vectors(maker, source_id: uuid.UUID, rows: list[tuple], vectors: np.ndarray, memberships) -> None:
    metas = [ChunkMeta(id=int(r[0]), ord=int(r[1]), heading=r[3] or "", kind=r[2], ref_ords=list(r[5] or [])) for r in rows]
    graph = await asyncio.to_thread(build, metas, memberships)
    smoothed = await asyncio.to_thread(graph.smooth, vectors)
    async with maker() as session:
        for start in range(0, len(rows), 500):
            await session.execute(
                text("UPDATE studio.rag_chunks SET graph_embedding = CAST(:v AS halfvec) WHERE id = :id"),
                [
                    {"id": int(rows[i][0]), "v": vector_literal(smoothed[i].tolist())}
                    for i in range(start, min(len(rows), start + 500))
                ],
            )
        await session.commit()


async def _describe_figures(maker, source: RagSource, title: str, limit: int | None) -> None:
    async with maker() as session:
        figures = list(
            await session.scalars(
                select(RagFigure).where(RagFigure.source_id == source.id).order_by(RagFigure.chunk_ord)
            )
        )
    if limit is not None:
        figures = figures[:limit]
    for index, figure in enumerate(figures, start=1):
        await _ensure_exists(maker, source.id)
        try:
            async with garage_client() as client:
                stored = await get_object(client, figure.storage_key)
            png = await asyncio.to_thread(_to_png, stored.content)
        except Exception:  # noqa: BLE001
            logger.exception("figure %s unreadable", figure.id)
            continue
        async with maker() as session:
            near = await session.scalar(
                text(
                    "SELECT content FROM studio.rag_chunks WHERE source_id = :s AND ord < :o "
                    "AND kind = 'text' ORDER BY ord DESC LIMIT 1"
                ),
                {"s": source.id, "o": figure.chunk_ord or 0},
            )
        description = await llm_client.describe_image(png, figure.caption, near or "")
        if not description:
            continue
        async with maker() as session:
            row = (
                await session.execute(
                    text("SELECT id, heading, graph_embedding::text FROM studio.rag_chunks WHERE source_id = :s AND ord = :o"),
                    {"s": source.id, "o": figure.chunk_ord},
                )
            ).first()
            await session.execute(
                text("UPDATE studio.rag_figures SET description = :d WHERE id = :id"),
                {"d": description[:2000], "id": figure.id},
            )
            if row is not None:
                content = figure_text(figure.ref, figure.caption, description, page=figure.page)
                vector = (await llm_client.embed([embed_text(title, row[1], content)], lane="ingest"))[0]
                graph_vec = vector
                if row[2]:
                    old = np.asarray([float(x) for x in row[2].strip("[]").split(",")], dtype=np.float32)
                    mixed = 0.65 * np.asarray(vector, dtype=np.float32) + 0.35 * old
                    graph_vec = (mixed / (np.linalg.norm(mixed) or 1)).tolist()
                await session.execute(
                    text(
                        "UPDATE studio.rag_chunks SET content = :c, embedding = CAST(:v AS halfvec), "
                        "graph_embedding = CAST(:g AS halfvec) WHERE id = :id"
                    ),
                    {"c": content, "v": vector_literal(vector), "g": vector_literal(graph_vec), "id": row[0]},
                )
            await session.commit()
            await source_lib.set_stage(session, source.id, "figures", 90 + int(9 * index / max(len(figures), 1)))


def _to_png(data: bytes, max_side: int = 1024) -> bytes:
    from PIL import Image

    image = Image.open(io.BytesIO(data)).convert("RGB")
    image.thumbnail((max_side, max_side))
    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


async def _pipeline(maker, source_id: uuid.UUID) -> None:
    from app.api.routes.settings import settings_service

    async with maker() as session:
        source = await _source(session, source_id)
        await _clear_partial(session, source_id)
        rag_conf = await settings_service.load(session, "rag")
    if source is None:
        return
    is_upload = source.scope == "upload"
    lane = "embed" if is_upload else "ingest"

    with tempfile.TemporaryDirectory(prefix="rag-") as workdir:
        path: str | None = None
        if source.input_key:
            path = os.path.join(workdir, Path(source.filename or "document").name or "document")
            async with garage_client() as client:
                await download_to_file(client, source.input_key, path)
            if is_upload and path.lower().endswith(".pdf"):
                pages = await asyncio.to_thread(pdf_page_count, path)
                if pages > rag_conf.upload_max_pages:
                    raise IngestError(f"That PDF has {pages} pages. The limit is {rag_conf.upload_max_pages}.")
        extra: list[Block] = []
        if source.scope == "chatbot":
            try:
                extra = portfolio.blocks_from(await portfolio.fetch())
            except Exception:  # noqa: BLE001
                logger.exception("portfolio unavailable during chatbot sync")
                if not path:
                    raise IngestError("The portfolio could not be read and there is no PDF to use.")
            extra += portfolio.studio_blocks()
        title, pages, count = await _parse_into_db(maker, source, path, extra)
    release_models()

    if count == 0:
        raise IngestError("No readable text was found. Scanned pages are not supported yet.")

    async with maker() as session:
        rows = await _chunk_rows(session, source_id)
        await source_lib.set_stage(session, source_id, "embedding", 30, title=title[:300], page_count=pages)
    vectors = await _embed_all(maker, source, title, rows, lane)

    async with maker() as session:
        await source_lib.set_stage(session, source_id, "linking", 78)
    memberships = await _link_entities(maker, source_id, rows)
    entity_lib.release_model()

    async with maker() as session:
        await source_lib.set_stage(session, source_id, "graph", 85)
    await _graph_vectors(maker, source_id, rows, vectors, memberships)

    async with maker() as session:
        figure_count = int(
            await session.scalar(text("SELECT count(*) FROM studio.rag_figures WHERE source_id = :s"), {"s": source_id})
            or 0
        )
        await source_lib.set_stage(
            session, source_id, "figures" if figure_count else "complete", 90,
            status="ready", ready_at=source_lib.now(), chunk_count=len(rows), figure_count=figure_count,
        )
        source = await _source(session, source_id)
        replaced: list[uuid.UUID] = []
        if source is not None and source.scope == "chatbot":
            replaced = await source_lib.go_live(session, source)
        elif source is not None and source.scope == "book":
            await source_lib.put_on_shelf(session, source)
        for old_id in replaced:
            try:
                await source_lib.delete_source(session, old_id)
            except Exception:  # noqa: BLE001
                logger.exception("old version %s not deleted yet; the sweeper retries", old_id)

    if figure_count and source is not None:
        await _describe_figures(maker, source, title, UPLOAD_FIGURE_LIMIT if is_upload else None)

    async with maker() as session:
        done = source_lib.now()
        extra_values = {}
        if is_upload:
            extra_values["expires_at"] = done + timedelta(minutes=rag_conf.upload_ttl_minutes)
        await source_lib.set_stage(session, source_id, "complete", 100, completed_at=done, **extra_values)
