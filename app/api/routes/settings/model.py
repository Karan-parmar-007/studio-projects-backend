from datetime import date, datetime

from sqlalchemy import Date, DateTime, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ServiceSetting(Base):
    """Per-product settings an admin edits (who may use it, where, limits). One JSON row
    per product; missing keys fall back to the product's defaults in code."""

    __tablename__ = "service_settings"
    __table_args__ = {"schema": "studio"}

    project: Mapped[str] = mapped_column(String(40), primary_key=True)
    data: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class UsageCounter(Base):
    """Daily use per product per subject ("u:<user id>" or "ip:<keyed hash>")."""

    __tablename__ = "usage_counters"
    __table_args__ = {"schema": "studio"}

    project: Mapped[str] = mapped_column(String(40), primary_key=True)
    subject: Mapped[str] = mapped_column(String(80), primary_key=True)
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
