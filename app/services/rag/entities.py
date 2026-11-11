"""Names in passages, for the graph and the entity search channel. No LLM calls.

GLiNER2 (205M, CPU) tags typed entities when the worker image has it; otherwise a
fast heuristic picks capitalised phrases, acronyms and technical tokens. Both
return (name, label) pairs; `norm()` is the key every match uses.
"""

from __future__ import annotations

import logging
import os
import re
import threading

logger = logging.getLogger(__name__)

LABELS = [
    "person",
    "organization",
    "place",
    "technology",
    "product",
    "concept",
    "process",
    "chemical",
    "body part",
    "event",
]
THRESHOLD = 0.5
BATCH = 8

_STOP = {
    "the", "a", "an", "and", "or", "of", "in", "on", "for", "to", "with", "this", "that", "these",
    "those", "it", "its", "is", "are", "was", "were", "be", "by", "as", "at", "from", "figure",
    "table", "chapter", "section", "page", "fig", "see", "also", "however", "therefore", "thus",
}

_model = None
_model_failed = False
_lock = threading.Lock()


def release_model() -> None:
    """Drop GLiNER2 (~1 GB) after a document is linked; it reloads in seconds for the next one."""
    global _model
    with _lock:
        _model = None
    import gc

    gc.collect()


def norm(name: str) -> str:
    value = re.sub(r"[^a-z0-9+#.]+", " ", name.lower()).strip(" .")
    return re.sub(r"\s+", " ", value)[:200]


def _gliner():
    global _model, _model_failed
    if _model_failed or os.environ.get("RAG_ENTITIES", "gliner").strip() != "gliner":
        return None
    with _lock:
        if _model is None and not _model_failed:
            try:
                from gliner2 import GLiNER2

                _model = GLiNER2.from_pretrained(os.environ.get("RAG_GLINER_MODEL", "fastino/gliner2-base-v1"))
            except Exception as exc:  # noqa: BLE001
                logger.info("GLiNER2 not available (%s); using the heuristic extractor", exc)
                _model_failed = True
    return _model


_CAPS = re.compile(r"\b([A-Z][a-zA-Z0-9\-]+(?:\s+(?:of|de|the)?\s*[A-Z][a-zA-Z0-9\-]+){0,4})")
_ACRONYM = re.compile(r"\b([A-Z]{2,}[0-9]*s?)\b")
_TECH = re.compile(r"\b([a-zA-Z]+[0-9]+[a-zA-Z0-9.\-]*|[a-z]+[A-Z][a-zA-Z0-9]+)\b")


def heuristic(text: str) -> list[tuple[str, str]]:
    found: dict[str, tuple[str, str]] = {}
    sentences = re.split(r"(?<=[.!?])\s+", text)
    for sentence in sentences:
        for match in _CAPS.finditer(sentence):
            name = match.group(1).strip()
            # The first word of a sentence is capitalised anyway: keep it only if multi-word.
            if match.start() == 0 and " " not in name:
                continue
            key = norm(name)
            if len(key) < 3 or key in _STOP:
                continue
            found.setdefault(key, (name, "concept"))
    for regex in (_ACRONYM, _TECH):
        for match in regex.finditer(text):
            key = norm(match.group(1))
            if len(key) >= 2 and key not in _STOP:
                found.setdefault(key, (match.group(1), "concept"))
    return list(found.values())


def extract(texts: list[str]) -> list[list[tuple[str, str]]]:
    """One list of (name, label) per text."""
    model = _gliner()
    if model is None:
        return [heuristic(text) for text in texts]
    out: list[list[tuple[str, str]]] = []
    for start in range(0, len(texts), 64):
        batch = [text[:2000] for text in texts[start : start + 64]]
        try:
            results = model.batch_extract_entities(batch, LABELS, batch_size=BATCH, threshold=THRESHOLD)
        except Exception as exc:  # noqa: BLE001
            logger.warning("GLiNER2 failed on a batch (%s); heuristic for it", exc)
            out.extend(heuristic(text) for text in batch)
            continue
        for text, result in zip(batch, results):
            pairs: dict[str, tuple[str, str]] = {}
            groups = (result or {}).get("entities") or {}
            for label, names in groups.items():
                for name in names or []:
                    value = name.get("text") if isinstance(name, dict) else str(name)
                    key = norm(value or "")
                    if len(key) >= 2 and key not in _STOP:
                        pairs.setdefault(key, (str(value).strip()[:200], str(label)[:40]))
            out.append(list(pairs.values()))
    return out


def query_norm(question: str) -> str:
    """Padded with spaces so `' ' || norm || ' '` matches whole words only."""
    return f" {norm(question)} "
