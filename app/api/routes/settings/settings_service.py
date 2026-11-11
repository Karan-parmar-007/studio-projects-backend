"""Who may use a product, where, and how much. Admin-edited, stored as one JSON row.

Access modes:
  everyone    signed out too (the public chatbot on the portfolio)
  signed_in   any signed-in account
  roles       only the listed roles: a global SSO role ("owner", "user", ...) or a
              Studio app role written "studio:<role>". Admins always pass.
"""

from __future__ import annotations

import hashlib
import hmac
import threading
from datetime import UTC, date, datetime
from typing import Literal

from fastapi import Request
from karan_sso import SsoIdentity
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routes.settings.model import ServiceSetting
from app.config import auth_settings, rag_settings
from app.core.errors import ConflictError, ForbiddenError, QuotaError, SignInRequiredError

Access = Literal["everyone", "signed_in", "roles"]


class Limits(BaseModel):
    model_config = ConfigDict(extra="ignore")

    # Questions per UTC day.
    daily_anonymous: int = Field(default=15, ge=0, le=10_000)
    daily_signed_in: int = Field(default=60, ge=0, le=10_000)
    # Refuse new questions once this many are already waiting in the gateway line.
    max_waiting_anonymous: int = Field(default=5, ge=0, le=20)
    max_waiting_signed_in: int = Field(default=12, ge=0, le=20)


class ChatbotSettings(BaseModel):
    model_config = ConfigDict(extra="ignore")

    access: Access = "everyone"
    roles: list[str] = Field(default_factory=list, max_length=20)
    # Where the bubble shows.
    on_studio: bool = True
    on_portfolio: bool = True
    # Tools that act for the user (Maps Scraper) need sign-in no matter what.
    tools_enabled: bool = True
    limits: Limits = Field(default_factory=Limits)


class RagProductSettings(BaseModel):
    model_config = ConfigDict(extra="ignore")

    access: Access = "signed_in"
    roles: list[str] = Field(default_factory=list, max_length=20)
    # Users may upload their own document (one at a time, deleted after an hour).
    uploads_enabled: bool = True
    upload_max_mb: int = Field(default=5, ge=1, le=5)
    upload_ttl_minutes: int = Field(default=60, ge=5, le=60)
    upload_max_pages: int = Field(default=60, ge=1, le=300)
    limits: Limits = Field(default_factory=lambda: Limits(daily_anonymous=10, daily_signed_in=40))


MODELS: dict[str, type[BaseModel]] = {"chatbot": ChatbotSettings, "rag": RagProductSettings}


async def load(session: AsyncSession, project: str) -> BaseModel:
    model = MODELS[project]
    if not isinstance(session, AsyncSession):
        return model()
    row = await session.get(ServiceSetting, project)
    return model.model_validate(row.data if row is not None else {})


async def save(session: AsyncSession, project: str, values: BaseModel) -> BaseModel:
    model = MODELS[project]
    clean = model.model_validate(values.model_dump())
    row = await session.get(ServiceSetting, project)
    if row is None:
        session.add(ServiceSetting(project=project, data=clean.model_dump()))
    else:
        row.data = clean.model_dump()
    await session.commit()
    return clean


def role_allowed(identity: SsoIdentity, roles: list[str]) -> bool:
    if identity.is_admin or identity.has_app_role(auth_settings.SSO_APP_ID, "admin"):
        return True
    wanted = {role.strip().lower() for role in roles if role.strip()}
    if identity.role and identity.role in wanted:
        return True
    app = auth_settings.SSO_APP_ID
    return any(f"{app}:{role}".lower() in wanted for role in identity.app_roles(app))


def check_access(settings: BaseModel, identity: SsoIdentity | None) -> None:
    access = getattr(settings, "access", "signed_in")
    if access == "everyone":
        return
    if identity is None:
        raise SignInRequiredError("Sign in to use this.")
    if access == "roles" and not role_allowed(identity, list(getattr(settings, "roles", []))):
        raise ForbiddenError("Your account does not have access to this yet.")


def client_ip(request: Request) -> str:
    """Caddy is the only proxy in front of us and appends the real client last."""
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[-1].strip()
    return request.client.host if request.client else "unknown"


def _ip_secret() -> bytes:
    seed = rag_settings.RAG_MEDIA_SECRET or auth_settings.FLOW_TOKEN or "studio-dev"
    return hashlib.sha256(("ip|" + seed).encode()).digest()


def subject_for(identity: SsoIdentity | None, request: Request) -> str:
    if identity is not None:
        return f"u:{identity.user_id}"[:80]
    digest = hmac.new(_ip_secret(), client_ip(request).encode(), hashlib.sha256).hexdigest()[:32]
    return f"ip:{digest}"


def today() -> date:
    return datetime.now(UTC).date()


async def take_daily(session: AsyncSession, project: str, subject: str, limit: int) -> int:
    """Count one use for today. Raises QuotaError past the limit. Returns uses left."""
    if not isinstance(session, AsyncSession):
        return limit
    if limit <= 0:
        raise QuotaError("This is not available right now. Try again tomorrow.")
    used = await session.scalar(
        text(
            """
            INSERT INTO studio.usage_counters (project, subject, day, used)
            VALUES (:project, :subject, :day, 1)
            ON CONFLICT (project, subject, day)
            DO UPDATE SET used = studio.usage_counters.used + 1
            WHERE studio.usage_counters.used < :limit
            RETURNING used
            """
        ),
        {"project": project, "subject": subject, "day": today(), "limit": limit},
    )
    await session.commit()
    if used is None:
        raise QuotaError("You have used today's questions. Come back tomorrow.")
    return max(limit - int(used), 0)


async def give_back(session: AsyncSession, project: str, subject: str) -> None:
    """Undo one use (the model never answered)."""
    if not isinstance(session, AsyncSession):
        return
    await session.execute(
        text(
            "UPDATE studio.usage_counters SET used = greatest(used - 1, 0) "
            "WHERE project = :project AND subject = :subject AND day = :day"
        ),
        {"project": project, "subject": subject, "day": today()},
    )
    await session.commit()


class InFlight:
    """One question at a time per person (in process: one uvicorn worker)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._busy: set[str] = set()

    def claim(self, key: str) -> None:
        with self._lock:
            if key in self._busy:
                raise ConflictError("Wait for the current answer to finish first.")
            self._busy.add(key)

    def release(self, key: str) -> None:
        with self._lock:
            self._busy.discard(key)


in_flight = InFlight()


async def check_line(lane: str, limits: Limits, signed_in: bool) -> int:
    """Refuse early when the gateway line is already long. Returns people ahead."""
    from app.services import llm_client

    board = await llm_client.queue_snapshot()
    lane_state = (board.get("lanes") or {}).get(lane) or {}
    ahead = int(lane_state.get("queued") or 0) + (1 if lane_state.get("running") else 0)
    cap = limits.max_waiting_signed_in if signed_in else limits.max_waiting_anonymous
    if int(lane_state.get("queued") or 0) >= cap:
        raise QuotaError("The assistant is busy with other people. Try again in a minute.", code="busy")
    return ahead
