"""Incident ORM model."""

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base


class Incident(Base):
    """A triaged anomaly: what happened, the likely cause and what to do next."""

    __tablename__ = "incidents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # The anomaly that opened the incident; later ones link via anomalies.incident_id
    anomaly_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("anomalies.id"), nullable=False, unique=True
    )
    service: Mapped[str | None] = mapped_column(String(128), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    anomaly_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    root_cause: Mapped[str | None] = mapped_column(Text, nullable=True)
    related_event_ids: Mapped[list[int] | None] = mapped_column(ARRAY(Integer), nullable=True)
    confidence: Mapped[str | None] = mapped_column(String(16), nullable=True)
    runbook: Mapped[str | None] = mapped_column(String(256), nullable=True)
    next_steps: Mapped[list[str] | None] = mapped_column(ARRAY(Text), nullable=True)
    # The ranked log patterns the report was based on (see triage/evidence.py)
    evidence: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB, nullable=True)
    triage_method: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="fallback", comment="llm | fallback"
    )
    llm_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="open", server_default="open"
    )
    # What fixed it, written when resolving; incident memory retrieves these
    resolution: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    anomaly = relationship("Anomaly", foreign_keys=[anomaly_id], lazy="joined")
    # Load explicitly (selectinload) where needed; "raise" catches accidental lazy IO
    anomalies = relationship(
        "Anomaly",
        foreign_keys="Anomaly.incident_id",
        order_by="Anomaly.window_start",
        lazy="raise",
        viewonly=True,
    )

    __table_args__ = (
        # Grouping looks up the open incident for a service on every triage
        Index("ix_incidents_service_status", "service", "status"),
        Index("ix_incidents_status_resolved_at", "status", "resolved_at"),
    )

    def __repr__(self) -> str:
        return f"<Incident(id={self.id}, status={self.status!r})>"
