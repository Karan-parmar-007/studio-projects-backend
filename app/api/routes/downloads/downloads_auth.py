"""Bearer-token gate for the release upload endpoint. The CI release workflow sends
STUDIO_RELEASE_TOKEN; browsers never hit this."""

from __future__ import annotations

import secrets
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status

from app.config import auth_settings


def require_release_token(authorization: Annotated[str | None, Header()] = None) -> None:
    expected = auth_settings.STUDIO_RELEASE_TOKEN.strip()
    if not expected:
        raise HTTPException(status_code=503, detail="Uploads are not configured")
    sent = ""
    if authorization and authorization.lower().startswith("bearer "):
        sent = authorization.split(" ", 1)[1].strip()
    if not sent or not secrets.compare_digest(sent, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="A valid release token is required",
            headers={"WWW-Authenticate": "Bearer"},
        )


ReleaseTokenDep = Annotated[None, Depends(require_release_token)]
