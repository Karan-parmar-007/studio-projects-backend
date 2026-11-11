"""Blocks -> retrieval chunks.

Small chunks (~300 tokens) because the answer model has a small context: five
of them plus the question must fit in about 1.5k tokens. Each chunk keeps its
heading path, which is prepended for embedding and keyword search (cheap
"contextual retrieval" without an LLM call per chunk). Figures and tables are
chunks of their own so they can be retrieved, cited and shown.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.services.rag.parse import Block, FigureDraft, caption_number

TARGET_TOKENS = 300
MAX_TOKENS = 420

_WORD = re.compile(r"\S+")
_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9(\"'])")
_FIG_REF = re.compile(r"\b(?:fig(?:ure)?s?\.?)\s*(\d+(?:\.\d+)*)", re.IGNORECASE)


def count_tokens(text: str) -> int:
    """~1.3 tokens per word for English with a BPE tokenizer. Good enough for budgets."""
    return max(1, round(len(_WORD.findall(text)) * 1.3))


@dataclass
class ChunkDraft:
    ord: int
    kind: str
    heading: str
    content: str
    page: int | None
    tokens: int
    figure: FigureDraft | None = None
    ref_ords: list[int] = field(default_factory=list)


def heading_text(headings: list[str]) -> str:
    return " > ".join(part for part in headings if part)[:500]


def _split_long(text: str, limit: int) -> list[str]:
    if count_tokens(text) <= limit:
        return [text]
    pieces: list[str] = []
    current: list[str] = []
    size = 0
    for sentence in _SENTENCE.split(text):
        sentence_tokens = count_tokens(sentence)
        if sentence_tokens > limit:
            words = sentence.split()
            step = max(1, int(limit / 1.3))
            for start in range(0, len(words), step):
                pieces.append(" ".join(words[start : start + step]))
            continue
        if size + sentence_tokens > limit and current:
            pieces.append(" ".join(current))
            current, size = [], 0
        current.append(sentence)
        size += sentence_tokens
    if current:
        pieces.append(" ".join(current))
    return pieces


def _split_table(text: str, limit: int) -> list[str]:
    if count_tokens(text) <= limit:
        return [text]
    lines = text.splitlines()
    header = [line for line in lines[:3] if line.strip().startswith("|")][:2]
    body = lines[len(header) :] if header else lines
    pieces: list[str] = []
    current: list[str] = []
    for line in body:
        current.append(line)
        if count_tokens("\n".join(header + current)) > limit:
            current.pop()
            if current:
                pieces.append("\n".join(header + current))
            current = [line]
    if current:
        pieces.append("\n".join(header + current))
    return pieces


def figure_ref(figure: FigureDraft) -> str:
    """How the document names this figure ("3", "11.6.1"); empty when it has no numbered caption."""
    return figure.doc_number or ""


def figure_label(ref: str, page: int | None) -> str:
    if ref:
        return f"Figure {ref}"
    return f"Figure on page {page}" if page else "Figure"


def figure_text(ref: str | None, caption: str, description: str = "", page: int | None = None) -> str:
    """"Figure 3: caption" (without doubling a caption that already starts "Figure 3")."""
    caption = (caption or "").strip()
    if caption and caption_number(caption) is not None:
        first = caption
    else:
        label = figure_label(ref or "", page)
        first = label + (f": {caption}" if caption else "")
    parts = [first]
    if description:
        parts.append(description)
    return "\n".join(parts)


def make_chunks(blocks: list[Block], target: int = TARGET_TOKENS, limit: int = MAX_TOKENS) -> list[ChunkDraft]:
    chunks: list[ChunkDraft] = []
    buffer: list[str] = []
    buffer_tokens = 0
    buffer_heading = ""
    buffer_page: int | None = None

    def emit(kind: str, heading: str, content: str, page: int | None, figure: FigureDraft | None = None) -> None:
        chunks.append(
            ChunkDraft(
                ord=len(chunks),
                kind=kind,
                heading=heading,
                content=content,
                page=page,
                tokens=count_tokens(content),
                figure=figure,
            )
        )

    def flush() -> None:
        nonlocal buffer, buffer_tokens, buffer_page
        if buffer:
            emit("text", buffer_heading, "\n".join(buffer), buffer_page)
        buffer, buffer_tokens, buffer_page = [], 0, None

    for block in blocks:
        heading = heading_text(block.headings)
        if block.kind == "figure":
            flush()
            ref = figure_ref(block.figure) if block.figure else None
            emit("figure", heading, figure_text(ref, block.text, page=block.page), block.page, block.figure)
            continue
        if block.kind == "table":
            flush()
            for piece in _split_table(block.text, limit):
                emit("table", heading, piece, block.page)
            continue
        if heading != buffer_heading:
            flush()
            buffer_heading = heading
        for piece in _split_long(block.text, limit):
            piece_tokens = count_tokens(piece)
            if buffer and buffer_tokens + piece_tokens > target:
                flush()
                buffer_heading = heading
            if buffer_page is None:
                buffer_page = block.page
            buffer.append(piece)
            buffer_tokens += piece_tokens
    flush()
    link_figures(chunks)
    return chunks


def link_figures(chunks: list[ChunkDraft]) -> None:
    """Text that says "Figure 3" points at that figure's chunk; a figure is also tied to
    the passage just before it (where the reader would see it)."""
    by_number: dict[str, int] = {}
    for chunk in chunks:
        if chunk.kind == "figure" and chunk.figure is not None and figure_ref(chunk.figure):
            by_number.setdefault(figure_ref(chunk.figure), chunk.ord)
    for chunk in chunks:
        if chunk.kind == "figure":
            continue
        refs = {by_number[n] for n in _FIG_REF.findall(chunk.content) if n in by_number}
        chunk.ref_ords = sorted(refs)
    for index, chunk in enumerate(chunks):
        if chunk.kind != "figure":
            continue
        for back in range(index - 1, max(-1, index - 3), -1):
            if chunks[back].kind == "text":
                if chunk.ord not in chunks[back].ref_ords:
                    chunks[back].ref_ords.append(chunk.ord)
                break


def embed_text(title: str, heading: str, content: str) -> str:
    """What gets embedded: the document and section a passage belongs to, then the passage."""
    head = f"Document: {title}\n" if title else ""
    if heading:
        head += f"Section: {heading}\n"
    return head + content
