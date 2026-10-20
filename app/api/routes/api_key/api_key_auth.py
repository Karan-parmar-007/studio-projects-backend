"""Bearer API-key auth for every /api/studio/v1/* route.

    from app.api.routes.api_key.api_key_auth import ApiKeyDep

    @router.post("/thing")
    async def thing(key: ApiKeyDep): ...  # key.owner_id is the user
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header

from app.api.deps import DbDep
from app.api.routes.api_key import api_key_service
from app.api.routes.api_key.model import ApiKey
from app.core.errors import NotFoundError


def bearer(authorization: str | None) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise NotFoundError("Send the API key as Authorization: Bearer sk-studio_...")
    return authorization.split(" ", 1)[1].strip()


async def require_api_key(
    db: DbDep, authorization: Annotated[str | None, Header()] = None
) -> ApiKey:
    return await api_key_service.key_from_secret(db, bearer(authorization))


ApiKeyDep = Annotated[ApiKey, Depends(require_api_key)]
