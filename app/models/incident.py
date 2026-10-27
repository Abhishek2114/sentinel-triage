"""Incident ORM model."""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base


class Incident(Base):
    """A triaged incident linked to an anomaly, with LLM-generated summary."""

    __tablename__ = "incidents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    anomaly_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("anomalies.id"), nullable=False, unique=True
    )
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    root_cause: Mapped[str | None] = mapped_column(Text, nullable=True)
    related_event_ids: Mapped[list[int] | None] = mapped_column(
        ARRAY(Integer), nullable=True
    )
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="open", server_default="open"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    anomaly = relationship("Anomaly", lazy="joined")

    def __repr__(self) -> str:
        return f"<Incident(id={self.id}, status={self.status!r})>"
