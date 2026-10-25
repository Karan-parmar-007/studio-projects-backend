from datetime import date, datetime

from sqlalchemy import CheckConstraint, Date, DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class GmapsDailyUsage(Base):
    """Companies a user took today (UTC). Scraped companies themselves are never stored."""

    __tablename__ = "gmaps_daily_usage"
    __table_args__ = (
        CheckConstraint("companies_used >= 0", name="ck_gmaps_daily_usage_non_negative"),
        {"schema": "studio"},
    )

    user_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    companies_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
