from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ProviderLink(Base):
    """One model link: for a user, for a project, a role (speech/llm) in a placement
    (local/remote). A URL plus an on/off toggle. Off means it is never dialled."""

    __tablename__ = "provider_links"
    __table_args__ = (
        UniqueConstraint(
            "owner_id", "project", "role", "placement", name="uq_provider_owner_project_role_place"
        ),
        {"schema": "studio"},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[str] = mapped_column(String(64), index=True)
    project: Mapped[str] = mapped_column(String(40), server_default="whisperflow")
    role: Mapped[str] = mapped_column(String(16))
    placement: Mapped[str] = mapped_column(String(16))
    url: Mapped[str] = mapped_column(Text, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
