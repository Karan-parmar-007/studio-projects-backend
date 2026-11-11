"""Short-lived links to figure images. Garage stays private: the backend checks the
signature and expiry, then streams the bytes. A link is useless after RAG_MEDIA_LINK_SECONDS,
and dead immediately once its document is deleted (the figure row is gone)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import struct
import time
import uuid

from app.config import auth_settings, rag_settings

MEDIA_PATH = "/api/studio/rag/media/"


def _secret() -> bytes:
    seed = rag_settings.RAG_MEDIA_SECRET or auth_settings.FLOW_TOKEN or "studio-dev-media"
    return hashlib.sha256(("media|" + seed).encode()).digest()


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def sign(figure_id: uuid.UUID, seconds: int | None = None, now: float | None = None) -> str:
    expires = int((now or time.time()) + (seconds or rag_settings.RAG_MEDIA_LINK_SECONDS))
    body = figure_id.bytes + struct.pack(">I", expires)
    mac = hmac.new(_secret(), body, hashlib.sha256).digest()[:16]
    return _b64(body + mac)


def verify(token: str, now: float | None = None) -> uuid.UUID | None:
    try:
        raw = _unb64(token)
    except Exception:  # noqa: BLE001
        return None
    if len(raw) != 16 + 4 + 16:
        return None
    body, mac = raw[:20], raw[20:]
    if not hmac.compare_digest(hmac.new(_secret(), body, hashlib.sha256).digest()[:16], mac):
        return None
    (expires,) = struct.unpack(">I", body[16:20])
    if expires < int(now or time.time()):
        return None
    return uuid.UUID(bytes=body[:16])


def url_for(figure_id: uuid.UUID) -> str:
    return MEDIA_PATH + sign(figure_id)


def storage_prefix(source_id: uuid.UUID) -> str:
    return f"studio/rag/{source_id}/"


def figure_key(source_id: uuid.UUID, number: int, ext: str) -> str:
    return f"{storage_prefix(source_id)}figures/{number:04d}.{ext}"


def input_key(source_id: uuid.UUID, filename: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in filename)[-120:] or "document"
    return f"{storage_prefix(source_id)}input/{safe}"
