"""Anomaly ORM model."""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class Anomaly(Base):
    """A detected anomaly window."""

    __tablename__ = "anomalies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    detector: Mapped[str] = mapped_column(
        String(64), nullable=False, comment="error_burst | iforest | lstm_ae"
    )
    score: Mapped[float] = mapped_column(Float, nullable=False)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    service: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    is_true_positive: Mapped[bool | None] = mapped_column(
        Boolean, nullable=True, comment="filled during evaluation"
    )
    # Several anomalies (a fault that flaps) can belong to one incident.
    # use_alter: incidents.anomaly_id points back here, so the FKs form a cycle
    incident_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey(
            "incidents.id", ondelete="SET NULL", use_alter=True, name="fk_anomalies_incident_id"
        ),
        nullable=True,
        index=True,
    )

    __table_args__ = (
        # Re-running detection over the same period updates rows instead of duplicating them
        UniqueConstraint(
            "detector",
            "service",
            "window_start",
            name="uq_anomalies_detector_service_window",
            postgresql_nulls_not_distinct=True,
        ),
    )

    def __repr__(self) -> str:
        return f"<Anomaly(id={self.id}, detector={self.detector!r}, score={self.score:.4f})>"
