"""Celery task definitions for detection and triage pipeline."""

from typing import Any

from celery import Task

from app.workers.celery_app import celery_app


@celery_app.task(name="detect_anomalies", bind=True, max_retries=3)
def detect_anomalies(
    self: Task, service: str | None = None, window_minutes: int = 1
) -> dict[str, Any]:
    """Run anomaly detection on recent log events.

    Phase 2 will fill in the full implementation:
      1. Query recent LogEvents from Postgres.
      2. Aggregate into per-minute features.
      3. Run Isolation Forest detector.
      4. Save detected anomalies to DB.
      5. Trigger triage for each new anomaly.
    """
    # TODO: Phase 2 implementation
    return {"status": "not_implemented", "service": service}


@celery_app.task(name="triage_anomaly", bind=True, max_retries=3)
def triage_anomaly(self: Task, anomaly_id: int) -> dict[str, Any]:
    """Run RAG triage for a single anomaly.

    Phase 4 will fill in the full implementation:
      1. Fetch anomaly and related log events.
      2. Retrieve relevant runbook chunks.
      3. Ask LLM for summary and root cause.
      4. Save Incident to DB.
      5. Push incident over WebSocket.
    """
    # TODO: Phase 4 implementation
    return {"status": "not_implemented", "anomaly_id": anomaly_id}
