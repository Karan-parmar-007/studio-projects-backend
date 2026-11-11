"""Question -> the few passages (and figures) worth showing the model.

  1. four channels, 40 each:  dense (chunk vectors)  keyword (Postgres full text)
                              graph-dense (smoothed vectors)  entities (names in the question)
  2. reciprocal rank fusion
  3. Personalized PageRank seeded with the fused list: adds connected passages
  4. cross-encoder rerank of the best 16 (gateway /rerank)
  5. blend rerank + graph + fused; refuse when even the best passage is weak
  6. keep the top passages that fit the context budget, plus up to 3 figures

Small sources (an upload, the chatbot corpus) are searched exactly; the big book
uses the HNSW indexes with iterative scans.
"""

from __future__ import annotations

import logging
import os
import re
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import llm_client
from app.services.rag import fusion
from app.services.rag import graph as graphs
from app.services.rag.entities import query_norm

logger = logging.getLogger(__name__)

CHANNEL_K = 40
EXPAND_K = 10
# Cross-encoder cost is linear in pool size: 16 x ~250 tokens is ~1-2 s on 4 CPU threads.
RERANK_POOL = 16
CONTEXT_TOKENS = 1400
MAX_PASSAGES = 5
MAX_FIGURES = 3
# Below this cross-encoder score the best passage does not answer the question.
# Refuse without asking the model only when a question is clearly not about the document:
# the best passage's reranker score OR its vector similarity is below the scope's floor.
# Measured on D2L (23 questions): on-topic rerank 0.81-0.94 / cosine 0.40-0.75, off-topic
# rerank 0.62-0.82 / cosine 0.21-0.50. The ranges overlap, so no single cut is right: the
# floors only catch the obvious cases and the answer model decides the grey zone (it replies
# NOT_FOUND, see answer.py). Override with RAG_REFUSE_RERANK_<SCOPE> / RAG_REFUSE_COSINE_<SCOPE>.
_FLOOR_DEFAULTS = {"book": (0.70, 0.30), "upload": (0.55, 0.25), "chatbot": (0.55, 0.25)}


def floors(scope: str | None) -> tuple[float, float]:
    rerank, cosine = _FLOOR_DEFAULTS.get(scope or "", (0.55, 0.25))
    key = (scope or "default").upper()
    return (
        float(os.environ.get(f"RAG_REFUSE_RERANK_{key}", rerank)),
        float(os.environ.get(f"RAG_REFUSE_COSINE_{key}", cosine)),
    )


# A supporting passage must score within this of the best one to be shown to the model.
TAIL_MARGIN = 0.12
EXACT_BELOW = 6000  # chunks; under this a sequential scan is exact and fast

_WORD = re.compile(r"[a-z0-9]+")

# Stage switches, for the eval harness's ablations (eval/rag_eval.py). All on in production.
STAGES = {"keyword": True, "graph_dense": True, "entities": True, "ppr": True, "rerank": True}


@dataclass
class Passage:
    id: int
    source_id: uuid.UUID
    ord: int
    kind: str
    heading: str
    content: str
    page: int | None
    tokens: int
    figure_id: uuid.UUID | None
    ref_ords: list[int]
    score: float = 0.0
    rerank: float | None = None


@dataclass
class Figure:
    id: uuid.UUID
    source_id: uuid.UUID
    number: int
    ref: str
    caption: str
    description: str
    page: int | None
    width: int
    height: int
    # 1-based number of the passage it belongs to (its own chunk, or the text that cites it).
    owner: int = 0


@dataclass
class Retrieval:
    passages: list[Passage] = field(default_factory=list)
    figures: list[Figure] = field(default_factory=list)
    confident: bool = False
    best: float = 0.0
    # Cosine similarity of the closest passage to the question.
    best_cosine: float = 0.0
    query_vector: list[float] | None = None
    # Per-candidate signals, for eval/rag_eval.py --fit: {"pool", "fused", "ppr", "ce"}.
    debug: dict = field(default_factory=dict)


def vector_literal(vector: list[float]) -> str:
    return "[" + ",".join(f"{value:.6f}" for value in vector) + "]"


def tsquery_terms(question: str, limit: int = 24) -> str:
    words = []
    for word in _WORD.findall(question.lower()):
        if len(word) >= 2 and word not in words:
            words.append(word)
    return " | ".join(words[:limit])


# ---------------------------------------------------------------- channels


async def _dense(
    session: AsyncSession, column: str, sources: list[uuid.UUID], vector: str, exact: bool
) -> tuple[list[int], float]:
    """Nearest chunks, and the cosine similarity of the closest one."""
    if exact:
        sql = f"""
            WITH c AS MATERIALIZED (
                SELECT id, {column} AS v FROM studio.rag_chunks
                WHERE source_id = ANY(:sources) AND {column} IS NOT NULL
            )
            SELECT id, v <=> CAST(:q AS halfvec) AS d FROM c ORDER BY d LIMIT :k
        """
    else:
        await session.execute(text("SET LOCAL hnsw.ef_search = 120"))
        await session.execute(text("SET LOCAL hnsw.iterative_scan = relaxed_order"))
        sql = f"""
            SELECT id, {column} <=> CAST(:q AS halfvec) AS d FROM studio.rag_chunks
            WHERE source_id = ANY(:sources) AND {column} IS NOT NULL
            ORDER BY {column} <=> CAST(:q AS halfvec) LIMIT :k
        """
    rows = (await session.execute(text(sql), {"sources": sources, "q": vector, "k": CHANNEL_K})).all()
    best = 1.0 - float(rows[0][1]) if rows else 0.0
    return [int(row[0]) for row in rows], best


_FIG_ASKED = re.compile(r"\b(?:fig(?:ure)?s?\.?)\s*(\d+(?:\.\d+)*)", re.IGNORECASE)


async def _named_figures(session: AsyncSession, sources: list[uuid.UUID], question: str) -> list[int]:
    """"What does Fig. 11.5.1 show?" -> that figure's chunk, by its exact number."""
    refs = list(dict.fromkeys(_FIG_ASKED.findall(question)))[:5]
    if not refs:
        return []
    rows = await session.execute(
        text(
            """
            SELECT c.id FROM studio.rag_chunks c JOIN studio.rag_figures f ON f.id = c.figure_id
            WHERE c.source_id = ANY(:sources) AND f.ref = ANY(:refs)
            """
        ),
        {"sources": sources, "refs": refs},
    )
    return [int(row[0]) for row in rows]


async def _keyword(session: AsyncSession, sources: list[uuid.UUID], question: str) -> list[int]:
    terms = tsquery_terms(question)
    if not terms:
        return []
    rows = await session.execute(
        text(
            """
            SELECT c.id FROM studio.rag_chunks c, to_tsquery('english', :terms) q
            WHERE c.source_id = ANY(:sources) AND c.tsv @@ q
            ORDER BY ts_rank_cd(c.tsv, q, 32) DESC LIMIT :k
            """
        ),
        {"sources": sources, "terms": terms, "k": CHANNEL_K},
    )
    return [int(row[0]) for row in rows]


async def _entities(session: AsyncSession, sources: list[uuid.UUID], question: str) -> list[int]:
    rows = await session.execute(
        text(
            """
            SELECT ce.chunk_id, sum(1.0 / ln(2 + e.df)) AS s
            FROM studio.rag_entities e
            JOIN studio.rag_chunk_entities ce ON ce.entity_id = e.id
            WHERE e.source_id = ANY(:sources) AND length(e.norm) >= 3
              AND :q LIKE ('% ' || e.norm || ' %')
            GROUP BY ce.chunk_id ORDER BY s DESC LIMIT :k
            """
        ),
        {"sources": sources, "q": query_norm(question), "k": CHANNEL_K},
    )
    return [int(row[0]) for row in rows]


# ---------------------------------------------------------------- graph cache

_GRAPHS: OrderedDict[uuid.UUID, graphs.SourceGraph] = OrderedDict()
_GRAPH_CACHE_SIZE = 8


def forget_graph(source_id: uuid.UUID) -> None:
    _GRAPHS.pop(source_id, None)


async def load_graph(session: AsyncSession, source_id: uuid.UUID) -> graphs.SourceGraph:
    cached = _GRAPHS.get(source_id)
    if cached is not None:
        _GRAPHS.move_to_end(source_id)
        return cached
    built = await build_graph(session, source_id)
    _GRAPHS[source_id] = built
    while len(_GRAPHS) > _GRAPH_CACHE_SIZE:
        _GRAPHS.popitem(last=False)
    return built


async def build_graph(session: AsyncSession, source_id: uuid.UUID) -> graphs.SourceGraph:
    chunk_rows = await session.execute(
        text("SELECT id, ord, heading, kind, ref_ords FROM studio.rag_chunks WHERE source_id = :s ORDER BY ord"),
        {"s": source_id},
    )
    chunks = [
        graphs.ChunkMeta(id=int(r[0]), ord=int(r[1]), heading=r[2] or "", kind=r[3], ref_ords=list(r[4] or []))
        for r in chunk_rows
    ]
    cap = graphs.entity_cap(len(chunks))
    member_rows = await session.execute(
        text(
            """
            SELECT ce.chunk_id, ce.entity_id, e.df FROM studio.rag_chunk_entities ce
            JOIN studio.rag_entities e ON e.id = ce.entity_id
            WHERE e.source_id = :s AND e.df BETWEEN 2 AND :cap
            """
        ),
        {"s": source_id, "cap": cap},
    )
    memberships = [(int(r[0]), int(r[1]), int(r[2])) for r in member_rows]
    return graphs.build(chunks, memberships)


# ---------------------------------------------------------------- main


async def _passages(session: AsyncSession, ids: list[int]) -> dict[int, Passage]:
    if not ids:
        return {}
    rows = await session.execute(
        text(
            """
            SELECT id, source_id, ord, kind, heading, content, page, tokens, figure_id, ref_ords
            FROM studio.rag_chunks WHERE id = ANY(:ids)
            """
        ),
        {"ids": ids},
    )
    return {
        int(r[0]): Passage(
            id=int(r[0]),
            source_id=r[1],
            ord=int(r[2]),
            kind=r[3],
            heading=r[4] or "",
            content=r[5],
            page=r[6],
            tokens=int(r[7] or 0),
            figure_id=r[8],
            ref_ords=list(r[9] or []),
        )
        for r in rows
    }


async def _figures_for(session: AsyncSession, chosen: list[Passage]) -> list[Figure]:
    owners: dict[uuid.UUID, int] = {}
    for index, passage in enumerate(chosen, start=1):
        if passage.figure_id:
            owners.setdefault(passage.figure_id, index)
    refs: dict[uuid.UUID, list[int]] = {}
    for passage in chosen:
        if passage.ref_ords:
            refs.setdefault(passage.source_id, []).extend(passage.ref_ords)
    for source_id, ords in refs.items():
        rows = await session.execute(
            text(
                "SELECT ord, figure_id FROM studio.rag_chunks WHERE source_id = :s AND ord = ANY(:ords) "
                "AND figure_id IS NOT NULL ORDER BY ord"
            ),
            {"s": source_id, "ords": sorted(set(ords))},
        )
        for ord_, figure_id in rows:
            owner = next(
                (i for i, p in enumerate(chosen, start=1) if p.source_id == source_id and ord_ in p.ref_ords), 0
            )
            owners.setdefault(figure_id, owner)
    unique: list[uuid.UUID] = list(owners)
    unique = unique[:MAX_FIGURES]
    if not unique:
        return []
    rows = await session.execute(
        text(
            "SELECT id, source_id, number, ref, caption, description, page, width, height "
            "FROM studio.rag_figures WHERE id = ANY(:ids)"
        ),
        {"ids": unique},
    )
    found = {
        r[0]: Figure(id=r[0], source_id=r[1], number=int(r[2]), ref=r[3] or "", caption=r[4] or "",
                     description=r[5] or "", page=r[6], width=int(r[7] or 0), height=int(r[8] or 0))
        for r in rows
    }
    for figure_id, figure in found.items():
        figure.owner = owners.get(figure_id, 0)
    return [found[i] for i in unique if i in found]


def rerank_text(passage: Passage) -> str:
    head = f"{passage.heading}\n" if passage.heading else ""
    return (head + passage.content)[:1000]


async def retrieve(
    session: AsyncSession,
    sources: list[uuid.UUID],
    question: str,
    *,
    total_chunks: int,
    budget: int = CONTEXT_TOKENS,
    max_passages: int = MAX_PASSAGES,
    query_vector: list[float] | None = None,
    scope: str | None = None,
) -> Retrieval:
    out = Retrieval()
    if not sources or not question.strip():
        return out
    vector = query_vector or (await llm_client.embed([question], query=True))[0]
    out.query_vector = vector
    literal = vector_literal(vector)
    exact = total_chunks < EXACT_BELOW

    dense, out.best_cosine = await _dense(session, "embedding", sources, literal, exact)
    graph_dense = (await _dense(session, "graph_embedding", sources, literal, exact))[0] if STAGES["graph_dense"] else []
    keyword = await _keyword(session, sources, question) if STAGES["keyword"] else []
    named = await _entities(session, sources, question) if STAGES["entities"] else []
    asked = await _named_figures(session, sources, question)
    fused = fusion.rrf([dense, keyword, graph_dense, named], weights=[1.0, 0.9, 0.8, 0.7])
    for cid in asked:  # a figure asked for by number always makes the pool, on top
        fused[cid] = max(fused.values(), default=0.0) + 1.0
    if not fused:
        return out

    # Graph expansion from the fused candidates (one graph per source; usually one source).
    seeds = {cid: score for cid, score in fused.items()}
    ppr: dict[int, float] = {}
    for source_id in sources if STAGES["ppr"] else []:
        try:
            graph = await load_graph(session, source_id)
        except Exception:  # noqa: BLE001
            logger.exception("graph load failed for %s", source_id)
            continue
        for cid, score in graph.ppr(seeds).items():
            ppr[cid] = ppr.get(cid, 0.0) + score
    candidates = fusion.top(fused, CHANNEL_K)
    candidates += fusion.top(ppr, EXPAND_K, exclude=candidates)

    pre = fusion.blend(candidates, fused, ppr, None)
    pool = fusion.top(pre, RERANK_POOL)
    rows = await _passages(session, pool)
    pool = [cid for cid in pool if cid in rows]

    scores = await llm_client.rerank(question, [rerank_text(rows[cid]) for cid in pool]) if STAGES["rerank"] else None
    ce = {cid: float(score) for cid, score in zip(pool, scores)} if scores else None
    final = fusion.blend(pool, fused, ppr, ce)
    for cid in asked:
        if cid in final:
            final[cid] = max(final.values()) + 1.0
    out.debug = {"pool": pool, "fused": fused, "ppr": ppr, "ce": ce or {}}
    ranked = fusion.top(final, len(pool))
    for cid in ranked:
        rows[cid].score = final[cid]
        rows[cid].rerank = ce.get(cid) if ce else None

    floor_rerank, floor_cosine = floors(scope)
    named_figure = any(cid in final for cid in asked)
    if ce:
        out.best = max(ce.values())
        out.confident = named_figure or (out.best >= floor_rerank and out.best_cosine >= floor_cosine)
    else:
        out.best = final[ranked[0]] if ranked else 0.0
        out.confident = named_figure or (bool(ranked) and out.best_cosine >= floor_cosine)

    used = 0
    keep_from = max(floor_rerank, out.best - TAIL_MARGIN)
    for cid in ranked:
        passage = rows[cid]
        if ce and passage.rerank is not None and passage.rerank < keep_from and out.passages and cid not in asked:
            continue  # clearly weaker than the best passage; keep it (and its figures) out
        cost = passage.tokens or len(passage.content) // 4
        if out.passages and used + cost > budget:
            continue
        out.passages.append(passage)
        used += cost
        if len(out.passages) >= max_passages:
            break
    out.figures = await _figures_for(session, out.passages)
    return out
