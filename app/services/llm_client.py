"""Talk to the model gateway for the chatbot and RAG: embeddings, reranking, chat.

Every call names its gateway lane and waits in line on its own connection
(X-Wait) instead of polling, so answers can stream even after queueing:

  embed   query embeddings + reranking (short, never behind an answer)
  ingest  bulk document embeddings and figure descriptions (yields to people)
  chat    chatbot answers
  rag     document Q&A answers

A full line (429) becomes QuotaError(code="busy"); an unreachable gateway becomes
ModelOfflineError. Neither ever carries the gateway URL.

Which gateway: like WhisperFlow, two links an admin edits (Admin > Assistant or Ask a
Document): "local" = Karan's PC (fast GPU) and "remote" = the LLM server. Answers, reranking
and figure descriptions use local while its link is on and its /health answers, otherwise
remote. Embeddings never move: they always come from RAG_EMBED_URL (or the remote gateway),
so a book and the questions asked about it are embedded the same way.
"""

from __future__ import annotations

import base64
import logging
import time
from typing import Any

import httpx
import numpy as np

from app.config import auth_settings, rag_settings
from app.core.errors import ModelOfflineError, QuotaError

logger = logging.getLogger(__name__)

# Qwen3-Embedding is instruction-aware: queries get a task line, documents none.
QUERY_INSTRUCTION = "Instruct: Given a question, retrieve passages that answer it\nQuery: "
EMBED_BATCH = 16

_models: dict[str, tuple[str, float]] = {}
_queue_cache: tuple[dict, float] | None = None

# ---------------------------------------------------------------- local first, remote second

LINK_OWNER = "studio"  # shared by every visitor, not one user's links
LINK_PROJECT = "assistant"  # chatbot and Ask a Document use the same pair
LINK_SLOTS = (("llm", "local"), ("llm", "remote"))
LINKS_TTL = 15.0
ALIVE_TTL = 30.0
DOWN_TTL = 20.0

_session_factory: Any = None
_links_cache: tuple[list[tuple[str, str]], float] | None = None
_alive: dict[str, tuple[bool, float]] = {}


def use_sessions(factory: Any) -> None:
    """Called at startup (API and worker) so links can be read from the database."""
    global _session_factory
    _session_factory = factory


def default_links() -> dict[tuple[str, str], str]:
    return {("llm", "local"): rag_settings.RAG_LOCAL_URL, ("llm", "remote"): rag_settings.gateway_url()}


def forget_links() -> None:
    global _links_cache
    _links_cache = None


async def _saved_links() -> list[tuple[str, str]]:
    """Enabled (placement, url) pairs, local first. Falls back to the env gateway."""
    global _links_cache
    now = time.monotonic()
    if _links_cache and now - _links_cache[1] < LINKS_TTL:
        return _links_cache[0]
    links: list[tuple[str, str]] = []
    if _session_factory is not None:
        try:
            from app.api.routes.model_links import model_links_service

            async with _session_factory() as session:
                rows = await model_links_service.ensure_links(
                    session, LINK_OWNER, LINK_PROJECT, LINK_SLOTS, default_links()
                )
            links = [(row.placement, row.url.strip().rstrip("/")) for row in rows if row.enabled and row.url.strip()]
        except Exception:  # noqa: BLE001
            logger.exception("could not read the assistant's model links; using the env gateway")
    if not links and rag_settings.gateway_url():
        links = [("remote", rag_settings.gateway_url())]
    _links_cache = (links, now)
    return links


async def _is_alive(url: str) -> bool:
    now = time.monotonic()
    known = _alive.get(url)
    if known and now - known[1] < (ALIVE_TTL if known[0] else DOWN_TTL):
        return known[0]
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            response = await client.get(f"{url}/health")
            body = response.json() if response.status_code < 500 else {}
            ok = response.status_code == 200 and isinstance(body.get("llm"), dict)
    except (httpx.HTTPError, ValueError):
        ok = False
    _alive[url] = (ok, now)
    if not ok:
        logger.info("local gateway is not answering; using the LLM server")
    return ok


def mark_down(url: str | None) -> None:
    """A call to this gateway just failed: skip it for DOWN_TTL seconds."""
    if url:
        _alive[url] = (False, time.monotonic())


async def gateways() -> list[str]:
    """Gateways to try, in order: the PC (when on and answering), then the LLM server."""
    out: list[str] = []
    for placement, url in await _saved_links():
        if placement == "local" and not await _is_alive(url):
            continue
        if url not in out:
            out.append(url)
    if not out:
        raise ModelOfflineError("The assistant is not connected to a model right now.")
    return out


def headers(lane: str, wait: float | None = None) -> dict[str, str]:
    out = {"X-Lane": lane, "X-Wait": str(int(wait if wait is not None else rag_settings.RAG_WAIT_SECONDS))}
    token = auth_settings.FLOW_TOKEN.strip()
    if token:
        out["X-Flow-Token"] = token
    return out


def base_url() -> str:
    url = rag_settings.gateway_url()
    if not url:
        raise ModelOfflineError("The assistant is not connected to a model right now.")
    return url


def embed_url() -> str:
    """Where vectors are built. RAG_EMBED_URL when set, otherwise the chat gateway."""
    url = rag_settings.RAG_EMBED_URL.strip().rstrip("/")
    return url or base_url()


def _raise_for(response: httpx.Response, what: str) -> None:
    if response.status_code == 429:
        raise QuotaError("The assistant is busy with other people. Try again in a minute.", code="busy")
    if response.status_code >= 400:
        logger.warning("gateway %s failed: %s %s", what, response.status_code, response.text[:300])
        raise ModelOfflineError("The model could not answer right now.")


def normalize(vector: list[float], dim: int | None = None) -> list[float]:
    arr = np.asarray(vector, dtype=np.float32)
    if dim and arr.shape[0] > dim:
        arr = arr[:dim]  # Matryoshka models keep their meaning in the leading dims
    norm = float(np.linalg.norm(arr))
    return (arr / norm).tolist() if norm else arr.tolist()


async def embed(texts: list[str], *, query: bool = False, lane: str = "embed") -> list[list[float]]:
    if not texts:
        return []
    dim = rag_settings.RAG_EMBED_DIM
    prepared = [(QUERY_INSTRUCTION + text) if query else text for text in texts]
    out: list[list[float]] = []
    wait = 600 if lane == "ingest" else rag_settings.RAG_WAIT_SECONDS
    try:
        async with httpx.AsyncClient(timeout=wait + 120) as client:
            for start in range(0, len(prepared), EMBED_BATCH):
                batch = prepared[start : start + EMBED_BATCH]
                response = await client.post(
                    f"{embed_url()}/api/embed",
                    json={
                        "model": rag_settings.RAG_EMBED_MODEL,
                        "input": batch,
                        "truncate": True,
                        "keep_alive": -1,
                    },
                    headers=headers(lane, wait),
                )
                _raise_for(response, "embed")
                vectors = response.json().get("embeddings") or []
                if len(vectors) != len(batch):
                    raise ModelOfflineError("The embedding model returned the wrong number of vectors.")
                out.extend(normalize(vector, dim) for vector in vectors)
    except httpx.HTTPError as exc:
        logger.warning("gateway embed unreachable: %s", exc)
        raise ModelOfflineError("The model could not answer right now.") from exc
    return out


async def rerank(query: str, documents: list[str]) -> list[float] | None:
    """Cross-encoder relevance in [0, 1] per document, or None if the gateway has no reranker."""
    if not documents:
        return []
    response = None
    try:
        candidates = await gateways()
    except ModelOfflineError:
        return None
    for url in candidates:
        try:
            async with httpx.AsyncClient(timeout=rag_settings.RAG_WAIT_SECONDS + 60) as client:
                response = await client.post(
                    f"{url}/rerank",
                    json={"query": query, "documents": documents},
                    headers=headers("embed"),
                )
        except httpx.HTTPError as exc:
            logger.warning("gateway rerank unreachable: %s", exc)
            mark_down(url)
            response = None
            continue
        if response.status_code == 404:
            response = None
            continue  # this gateway has no reranker; try the next
        break
    if response is None:
        return None
    _raise_for(response, "rerank")
    scores = [0.0] * len(documents)
    for item in response.json().get("results") or []:
        index = int(item.get("index", -1))
        if 0 <= index < len(scores):
            scores[index] = float(item.get("relevance_score", 0.0))
    return scores


async def queue_snapshot() -> dict:
    """The gateway's lanes, cached for a second. Empty dict if unreachable."""
    global _queue_cache
    now = time.monotonic()
    if _queue_cache and now - _queue_cache[1] < 1.0:
        return _queue_cache[0]
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.get(f"{(await gateways())[0]}/queue")
            board = response.json().get("llm") or {}
    except (httpx.HTTPError, ValueError, ModelOfflineError):
        board = {}
    _queue_cache = (board, now)
    return board


async def chat_model_name(url: str | None = None) -> str:
    """RAG_CHAT_MODEL, else whatever that gateway serves by default (cached 10 minutes)."""
    if rag_settings.RAG_CHAT_MODEL.strip():
        return rag_settings.RAG_CHAT_MODEL.strip()
    url = url or (await gateways())[0]
    known = _models.get(url)
    if known and time.monotonic() - known[1] < 600:
        return known[0]
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(f"{url}/health")
            name = str((response.json().get("llm") or {}).get("default_model") or "")
    except (httpx.HTTPError, ValueError) as exc:
        mark_down(url)
        raise ModelOfflineError("The model could not answer right now.") from exc
    if not name:
        raise ModelOfflineError("The model could not answer right now.")
    _models[url] = (name, time.monotonic())
    return name


async def chat_model(lane: str, *, max_tokens: int | None = None, temperature: float = 0.6) -> Any:
    model, _url = await chat_model_for(lane, max_tokens=max_tokens, temperature=temperature)
    return model


async def chat_model_for(lane: str, *, max_tokens: int | None = None, temperature: float = 0.6) -> tuple[Any, str]:
    """A LangChain ChatOllama on the first live gateway (the PC when it is up), and that
    gateway's URL so a failure can be pinned on it. Thinking is off: the gateway forces
    think=false too, and a 4B model answers better without a hidden essay."""
    from langchain_ollama import ChatOllama

    url = (await gateways())[0]
    # Time in the gateway line + up to 15 minutes of silence while the CPU model reads the prompt
    # (the gateway allows OLLAMA_STREAM_TIMEOUT_SECONDS=900). Progress shows meanwhile.
    kwargs = {"headers": headers(lane), "timeout": rag_settings.RAG_WAIT_SECONDS + 960}
    model = ChatOllama(
        model=await chat_model_name(url),
        base_url=url,
        reasoning=False,
        temperature=temperature,
        top_p=0.8,
        top_k=20,
        num_ctx=rag_settings.RAG_NUM_CTX or None,
        num_predict=max_tokens or rag_settings.RAG_MAX_ANSWER_TOKENS,
        keep_alive=-1,
        client_kwargs=kwargs,
        async_client_kwargs=kwargs,
    )
    return model, url


def gateway_error(exc: Exception, url: str | None = None) -> Exception:
    """Map an error raised inside LangChain/ollama to our domain errors. A gateway that
    could not be reached is skipped for a while, so the next question goes to the other one."""
    status = getattr(exc, "status_code", None)
    if url and (isinstance(exc, httpx.HTTPError) or status in (502, 503, 504, 520, 521, 522, 523, 524, 530)):
        mark_down(url)
    if status == 429:
        return QuotaError("The assistant is busy with other people. Try again in a minute.", code="busy")
    if isinstance(exc, (QuotaError, ModelOfflineError)):
        return exc
    return ModelOfflineError("The model could not answer right now.")


DESCRIBE_PROMPT = (
    "Describe this figure from a document for someone who cannot see it. "
    "Say what kind of figure it is, then every label, number, axis, part, and relationship it shows. "
    "Plain sentences, at most 120 words. Do not guess facts that are not visible."
)


async def describe_image(image: bytes, caption: str = "", context: str = "") -> str:
    """Figure description on the ingest lane (low priority). Empty string on failure."""
    hint = ""
    if caption:
        hint += f"\nIts caption: {caption[:300]}"
    if context:
        hint += f"\nText near it: {context[:500]}"
    body = {
        "model": "",
        "stream": False,
        "think": False,
        "keep_alive": -1,
        "options": {
            "temperature": 0.2,
            "num_predict": 220,
            **({"num_ctx": rag_settings.RAG_NUM_CTX} if rag_settings.RAG_NUM_CTX else {}),
        },
        "messages": [
            {
                "role": "user",
                "content": DESCRIBE_PROMPT + hint,
                "images": [base64.b64encode(image).decode()],
            }
        ],
    }
    try:
        candidates = await gateways()
    except ModelOfflineError:
        return ""
    for url in candidates:  # the PC's GPU first: a figure takes seconds there, minutes on the server
        try:
            body["model"] = await chat_model_name(url)
            async with httpx.AsyncClient(timeout=900) as client:
                response = await client.post(f"{url}/api/chat", json=body, headers=headers("ingest", 600))
            if response.status_code >= 400:
                logger.info("figure description failed: %s", response.status_code)
                if response.status_code >= 500:
                    mark_down(url)
                    continue
                return ""
            return str((response.json().get("message") or {}).get("content") or "").strip()
        except (httpx.HTTPError, ValueError, ModelOfflineError) as exc:
            logger.info("figure description failed: %s", exc)
            mark_down(url)
    return ""
