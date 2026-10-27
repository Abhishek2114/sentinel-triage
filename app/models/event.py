"""LogEvent ORM model."""

from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class LogEvent(Base):
    """A single parsed log line stored in Postgres."""

    __tablename__ = "log_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    service: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    level: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    template_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    raw_line: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        Index("ix_log_events_service_level", "service", "level"),
        Index("ix_log_events_timestamp_service", "timestamp", "service"),
    )

    def __repr__(self) -> str:
        return (
            f"<LogEvent(id={self.id}, service={self.service!r}, "
            f"level={self.level!r}, ts={self.timestamp})>"
        )
