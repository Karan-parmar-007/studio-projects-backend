from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ServiceSwitch(Base):
    """One row per product. Off means nobody can call it."""

    __tablename__ = "service_switches"
    __table_args__ = {"schema": "studio"}

    project: Mapped[str] = mapped_column(String(40), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
