from __future__ import annotations

import hashlib
import secrets


def new_api_secret() -> str:
    return "sk-studio_" + secrets.token_urlsafe(32)


def hash_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def prefix_of(secret: str) -> str:
    return secret[:16]
