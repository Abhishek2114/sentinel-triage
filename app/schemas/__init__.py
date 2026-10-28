"""Pydantic request/response schemas."""

from datetime import datetime
from typing import Literal, get_args

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
    incident_id: int | None = None

    model_config = {"from_attributes": True}


DetectorName = Literal["error_burst", "iforest"]


class DetectRequest(BaseModel):
    """Request body for running detection on stored events."""

    service: str | None = Field(None, description="Only this service (default: all)")
    since: datetime | None = Field(None, description="Inclusive lower bound (default: all)")
    until: datetime | None = Field(None, description="Exclusive upper bound (default: all)")
    window_minutes: int = Field(1, ge=1, le=60)
    detectors: list[DetectorName] = Field(
        default_factory=lambda: list(get_args(DetectorName)), min_length=1
    )


class DetectResponse(BaseModel):
    """Result of a detection run."""

    services: list[str]
    windows_scored: int
    new_anomalies: int
    anomalies: list[AnomalyRead]


# ── Incident ──────────────────────────────────────────


IncidentStatus = Literal["open", "resolved"]


class EvidencePattern(BaseModel):
    """A log pattern ranked as unusual during the anomaly."""

    template: str
    count: int
    baseline_count: int = Field(description="Occurrences in the hour before")
    errors: int
    lift: float = Field(description="Rate now / rate in the hour before")
    example: str
    event_ids: list[int]


class IncidentRead(BaseModel):
    """Schema for incident responses."""

    id: int
    anomaly_id: int = Field(description="The anomaly that opened the incident")
    service: str | None = None
    started_at: datetime
    last_seen_at: datetime
    anomaly_count: int = Field(description="Anomalies grouped into this incident")
    summary: str
    root_cause: str | None = None
    confidence: Literal["low", "medium", "high"] | None = None
    runbook: str | None = None
    next_steps: list[str] | None = None
    evidence: list[EvidencePattern] | None = None
    related_event_ids: list[int] | None = None
    triage_method: Literal["llm", "fallback"]
    llm_model: str | None = None
    status: IncidentStatus
    resolution: str | None = None
    resolved_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class SimilarIncident(BaseModel):
    """A resolved incident whose log patterns match (incident memory)."""

    id: int
    service: str | None
    summary: str
    runbook: str | None
    resolution: str | None
    resolved_at: datetime | None
    score: float = Field(description="Reciprocal-rank-fusion score; higher = closer")

    model_config = {"from_attributes": True}


class IncidentDetail(IncidentRead):
    """Incident with its grouped anomalies and evidence log lines."""

    anomaly: AnomalyRead | None = None
    anomalies: list[AnomalyRead] = Field(default_factory=list)
    related_events: list[LogEventRead] | None = None
    similar_incidents: list[SimilarIncident] = Field(default_factory=list)


class IncidentUpdate(BaseModel):
    status: IncidentStatus
    resolution: str | None = Field(
        None, max_length=4000, description="What fixed it; incident memory shows this later"
    )


class RunbookRead(BaseModel):
    name: str
    markdown: str


class RunbookIndexResponse(BaseModel):
    chunks: int
    embedding_model: str


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


# ── Overview / config ────────────────────────────────


ServiceStatus = Literal["incident", "elevated", "healthy", "quiet"]
VerdictLevel = Literal["incident", "warning", "ok", "no_data"]


class ServiceHealth(BaseModel):
    service: str
    status: ServiceStatus
    events: int
    errors: int
    error_rate: float
    last_seen: datetime | None
    open_incident_id: int | None = None
    # Errors per minute over the window, oldest first
    errors_per_minute: list[int]
    events_per_minute: list[int]


class Verdict(BaseModel):
    level: VerdictLevel
    headline: str
    detail: str


class Overview(BaseModel):
    verdict: Verdict
    as_of: datetime
    # True when there is no recent data and the window was moved to the
    # newest stored event (replayed or historical logs)
    anchored: bool
    window_minutes: int
    services: list[ServiceHealth]
    services_truncated: bool
    open_incidents: list[IncidentRead]


class PublicConfig(BaseModel):
    version: str
    demo_mode: bool
    read_requires_key: bool
    write_requires_key: bool
    llm_provider: str | None
    llm_calls_today: int
    llm_calls_limit: int


class DemoRunResponse(BaseModel):
    status: Literal["started"]
    lines: int
    services: list[str]
    fault: str
    detail: str


# ── Health ────────────────────────────────────────────


class HealthResponse(BaseModel):
    status: str = "ok"
    version: str = "0.1.0"
    db: str = "unknown"
