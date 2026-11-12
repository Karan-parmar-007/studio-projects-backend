"""Answering from retrieved passages, streamed as NDJSON events.

Event lines ({"type": ..., "data": {...}}):
  status   {"stage": "searching" | "waiting" | "writing", "ahead": n}
  sources  {"sources": [...], "figures": [...]}         before the first token
  token    {"text": "..."}
  action   {...}                                          chatbot tools (e.g. a Maps run to confirm)
  done     {"cached": bool, "found": bool}
  error    {"message": str, "code": str}

The system prompt is fixed text first so Ollama reuses its prompt cache across
questions; passages and the question come after it.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
import unicodedata
import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.rag import media
from app.services.rag.chunk import figure_label
from app.services.rag.retrieve import Figure, Passage, Retrieval, vector_literal

NOT_FOUND = "I could not find that in the document. Try asking it another way, or about a specific section."

RAG_SYSTEM = """You answer questions about a document using only the numbered passages you are given.

Rules:
- Use only facts from the passages. Never guess.
- If the passages do not answer the question at all, reply with exactly NOT_FOUND and nothing else. If they answer part of it, answer that part and say what the document does not cover.
- Cite the passages you used with their numbers in square brackets, like [1] or [2][3], right after the sentence they support.
- When a figure helps, name it as it appears in the passages (for example "Figure 3"); the reader can see the figures.
- Be clear and short: at most 6 sentences, or a short list when the answer is steps or items.
- Write plain Markdown. No headings.
"""


HEARTBEAT = object()
_ANSWER_LANES = ("voice", "chat", "rag")


async def with_heartbeat(source, every: float = 3.0):
    """Yield the source's items, plus HEARTBEAT whenever nothing arrived for `every` seconds
    (waiting in line, or the model still reading the question). The source is consumed in one
    background task, so LangChain/LangGraph context stays in a single task."""
    queue: asyncio.Queue = asyncio.Queue()

    async def pump() -> None:
        try:
            async for item in source:
                await queue.put(("item", item))
            await queue.put(("done", None))
        except BaseException as exc:  # noqa: BLE001 - re-raised in the consumer
            await queue.put(("error", exc))

    task = asyncio.create_task(pump())
    try:
        while True:
            try:
                kind, value = await asyncio.wait_for(queue.get(), every)
            except TimeoutError:
                yield HEARTBEAT
                continue
            if kind == "item":
                yield value
            elif kind == "done":
                return
            else:
                raise value
    finally:
        if not task.done():
            task.cancel()


async def waiting_status(started: float) -> str:
    """"In line: N ahead" while other answers are being written (the LLM server writes one at a
    time), else "thinking" (the model is reading the question). Seconds since the request."""
    from app.services import llm_client

    board = await llm_client.queue_snapshot()
    lanes = board.get("lanes") or {}
    busy = sum(
        (1 if (lanes.get(name) or {}).get("running") else 0) + int((lanes.get(name) or {}).get("queued") or 0)
        for name in _ANSWER_LANES
    )
    ahead = max(busy - 1, 0)  # this request is one of them
    waited = int(time.monotonic() - started)
    return event("status", stage="waiting" if ahead else "thinking", ahead=ahead, waited=waited)


def event(kind: str, /, **data: Any) -> str:
    return json.dumps({"type": kind, "data": data}, default=str, ensure_ascii=False) + "\n"


def snippet(text_value: str, limit: int = 220) -> str:
    flat = re.sub(r"\s+", " ", text_value).strip()
    return flat if len(flat) <= limit else flat[: limit - 1].rsplit(" ", 1)[0] + "…"


def source_list(passages: list[Passage], titles: dict[uuid.UUID, str] | None = None) -> list[dict]:
    return [
        {
            "n": index,
            "heading": passage.heading,
            "page": passage.page,
            "kind": passage.kind,
            "snippet": snippet(passage.content),
            "document": (titles or {}).get(passage.source_id, ""),
        }
        for index, passage in enumerate(passages, start=1)
    ]


def figure_list(figures: list[Figure]) -> list[dict]:
    return [
        {
            "id": str(figure.id),
            "url": media.url_for(figure.id),
            "label": figure_label(figure.ref, figure.page),
            "caption": figure.caption,
            "page": figure.page,
            "width": figure.width,
            "height": figure.height,
            "n": figure.owner,
        }
        for figure in figures
    ]


NOT_FOUND_MARK = "NOT_FOUND"
# Small models often refuse in words instead of the marker: "The provided passages do not
# contain information about X." Only a first sentence of that shape counts; a partial answer
# ("...do not explicitly state why, but ...") does not.
_REFUSAL = re.compile(
    r"^\W*(the\s+)?(provided\s+|given\s+)?(passages?|document|text|context)\s+(do|does)\s+not\s+"
    r"(contain|cover|mention|include|discuss|provide|address|have)\b"
    r"|^\W*(i\s+)?(could|can)\s*not\s+find\b|^\W*there\s+is\s+no\s+information\b",
    re.IGNORECASE,
)
_SENTENCE_END = re.compile(r"[.!?](\s|$)")


def first_sentence(text_value: str) -> str | None:
    """The first sentence once it is complete (or 220 chars), else None (keep reading)."""
    head = text_value.lstrip()
    match = _SENTENCE_END.search(head)
    if match:
        return head[: match.end()]
    return head if len(head) >= 220 else None


def is_refusal(text_value: str) -> bool:
    head = text_value.lstrip()
    if head.startswith(NOT_FOUND_MARK) or not head:
        return True
    sentence = first_sentence(head) or head
    if not _REFUSAL.search(sentence):
        return False
    # "...do not contain X. However, Y [2]." is a partial answer: keep it. A refusal instead goes
    # on to say what the passages cover ("They discuss SageMaker..., but none mention baking").
    rest = head[len(sentence):].lstrip()
    return not re.match(r"(however|that said|still|nevertheless)\b", rest, re.IGNORECASE)
_CITE = re.compile(r"\[(\d{1,2})\]")


def cited(text_value: str) -> set[int]:
    return {int(n) for n in _CITE.findall(text_value)}


def keep_cited(text_value: str, sources: list[dict], figures: list[dict]) -> tuple[list[dict], list[dict]]:
    """Only the passages the answer cites, and their figures. Nothing cited: keep everything."""
    numbers = cited(text_value)
    if not numbers:
        return sources, figures
    return (
        [item for item in sources if item["n"] in numbers],
        [item for item in figures if item.get("n") in numbers or not item.get("n")],
    )


def context_block(passages: list[Passage]) -> str:
    parts = []
    for index, passage in enumerate(passages, start=1):
        where = []
        if passage.heading:
            where.append(f"section: {passage.heading}")
        if passage.page:
            where.append(f"page {passage.page}")
        label = f"[{index}]" + (f" ({'; '.join(where)})" if where else "")
        parts.append(f"{label}\n{passage.content.strip()}")
    return "\n\n".join(parts)


def rag_messages(retrieval: Retrieval, question: str, history: list[dict] | None = None) -> list[tuple[str, str]]:
    messages: list[tuple[str, str]] = [("system", RAG_SYSTEM)]
    for turn in (history or [])[-4:]:
        role = "assistant" if turn.get("role") == "assistant" else "human"
        messages.append((role, str(turn.get("content") or "")[:800]))
    messages.append(("human", f"Passages:\n\n{context_block(retrieval.passages)}\n\nQuestion: {question}"))
    return messages


_FOLLOW_UP = re.compile(
    r"^(and|but|also|so|then|what about|how about|why|more)\b|\b(it|its|that|this|these|those|they|them|"
    r"the (first|second|third|last|previous|above) one)\b",
    re.IGNORECASE,
)


def search_query(question: str, history: list[dict] | None) -> str:
    """A follow-up ("and the second one?", "why is that?") is searched together with the previous
    question. A short standalone question ("what is supervised learning") is searched alone."""
    if not history or len(question.split()) > 8 or not _FOLLOW_UP.search(question.strip()):
        return question
    for turn in reversed(history):
        if turn.get("role") == "user" and turn.get("content"):
            return f"{str(turn['content'])[:300]} {question}"
    return question


# ---------------------------------------------------------------- answer cache


# Bump when retrieval or prompting changes: answers cached under older logic are then never
# replayed (they would otherwise live until the document version changes).
CACHE_VERSION = 3


def normalize_question(question: str) -> str:
    value = unicodedata.normalize("NFKC", question).lower()
    value = re.sub(r"[^\w\s]", " ", value)
    return f"v{CACHE_VERSION}|" + re.sub(r"\s+", " ", value).strip()[:490]


async def cached_answer(
    session: AsyncSession, source_id: uuid.UUID, question: str, vector: list[float] | None
) -> dict | None:
    if not isinstance(session, AsyncSession):
        return None
    qnorm = normalize_question(question)
    row = (
        await session.execute(
            text("SELECT id, answer FROM studio.rag_answer_cache WHERE source_id = :s AND qnorm = :q"),
            {"s": source_id, "q": qnorm},
        )
    ).first()
    if row is None and vector is not None:
        row = (
            await session.execute(
                text(
                    """
                    SELECT id, answer FROM studio.rag_answer_cache
                    WHERE source_id = :s AND question_embedding IS NOT NULL AND qnorm LIKE :prefix
                      AND (question_embedding <=> CAST(:v AS halfvec)) < 0.03
                    ORDER BY question_embedding <=> CAST(:v AS halfvec) LIMIT 1
                    """
                ),
                {"s": source_id, "v": vector_literal(vector), "prefix": f"v{CACHE_VERSION}|%"},
            )
        ).first()
    if row is None:
        return None
    await session.execute(text("UPDATE studio.rag_answer_cache SET hits = hits + 1 WHERE id = :id"), {"id": row[0]})
    await session.commit()
    return row[1]


async def store_answer(
    session: AsyncSession, source_id: uuid.UUID, question: str, vector: list[float] | None, answer: dict
) -> None:
    if not isinstance(session, AsyncSession):
        return
    await session.execute(
        text(
            """
            INSERT INTO studio.rag_answer_cache (source_id, qnorm, question_embedding, answer)
            VALUES (:s, :q, CAST(:v AS halfvec), CAST(:a AS jsonb))
            ON CONFLICT (source_id, qnorm) DO NOTHING
            """
        ),
        {
            "s": source_id,
            "q": normalize_question(question),
            "v": vector_literal(vector) if vector else None,
            "a": json.dumps(answer, default=str),
        },
    )
    await session.commit()


def replay(answer: dict) -> list[str]:
    """Events for a cached answer, with fresh figure links."""
    figures = []
    for item in answer.get("figures") or []:
        try:
            figures.append({**item, "url": media.url_for(uuid.UUID(item["id"]))})
        except (KeyError, ValueError):
            continue
    return [
        event("sources", sources=answer.get("sources") or [], figures=figures),
        event("token", text=answer.get("text") or ""),
        event("done", cached=True, found=bool(answer.get("found", True))),
    ]
