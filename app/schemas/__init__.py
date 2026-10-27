"""Pydantic request/response schemas."""

from datetime import datetime

from pydantic import BaseModel, Field

# ── LogEvent ──────────────────────────────────────────


class LogEventCreate(BaseModel):
    """Schema for creating a log event (from parser)."""

    timestamp: datetime
    service: str = Field(..., max_length=128)
    level: str = Field(..., max_length=16)
    message: str
    template_id: str | None = None
    raw_line: str | None = None


class LogEventRead(LogEventCreate):
    """Schema returned by the API."""

    id: int
    created_at: datetime

    model_config = {"from_attributes": True}


# ── Anomaly ───────────────────────────────────────────


class AnomalyRead(BaseModel):
    """Schema for anomaly responses."""

    id: int
    detected_at: datetime
    detector: str
    score: float
    window_start: datetime
    window_end: datetime
    service: str | None = None
    is_true_positive: bool | None = None

    model_config = {"from_attributes": True}


# ── Incident ──────────────────────────────────────────


class IncidentRead(BaseModel):
    """Schema for incident responses."""

    id: int
    anomaly_id: int
    summary: str
    root_cause: str | None = None
    related_event_ids: list[int] | None = None
    status: str
    created_at: datetime

    model_config = {"from_attributes": True}


class IncidentDetail(IncidentRead):
    """Incident with linked anomaly data."""

    anomaly: AnomalyRead | None = None
    related_events: list[LogEventRead] | None = None


# ── Ingest ────────────────────────────────────────────


class IngestRequest(BaseModel):
    """Request body for the /ingest endpoint."""

    file_path: str | None = Field(
        None, description="Path to a log file on the server, inside the ingest root (data/)."
    )
    lines: list[str] | None = Field(
        None, description="Raw log lines to ingest directly."
    )


class IngestResponse(BaseModel):
    """Response after ingesting logs."""

    ingested: int
    errors: int = 0


# ── Health ────────────────────────────────────────────


class HealthResponse(BaseModel):
    status: str = "ok"
    version: str = "0.1.0"
    db: str = "unknown"
