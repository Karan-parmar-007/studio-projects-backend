from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Download(Base):
    """One installable binary for a product on a platform. The bytes live in Garage;
    this row is the metadata the site lists and the key to fetch them by."""

    __tablename__ = "downloads"
    __table_args__ = (
        UniqueConstraint("product", "platform", name="uq_downloads_product_platform"),
        {"schema": "studio"},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    product: Mapped[str] = mapped_column(String(40), index=True)
    platform: Mapped[str] = mapped_column(String(20))
    filename: Mapped[str] = mapped_column(String(200))
    content_type: Mapped[str] = mapped_column(String(100), default="application/octet-stream")
    size: Mapped[int] = mapped_column(BigInteger, default=0)
    version: Mapped[str] = mapped_column(String(40), default="")
    object_key: Mapped[str] = mapped_column(String(300))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
