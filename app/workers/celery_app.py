"""Celery application factory."""

from celery import Celery

from app.core.config import get_settings

settings = get_settings()

celery_app = Celery(
    "sentinel_triage",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    # Run with `celery -A app.workers.celery_app beat` (the `beat` service in Compose)
    beat_schedule={
        "detect-recent-anomalies": {
            "task": "detect_anomalies",
            "schedule": settings.detection_interval_seconds,
            "kwargs": {"lookback_minutes": settings.detection_lookback_minutes},
        },
    },
)

celery_app.autodiscover_tasks(["app.workers"])
