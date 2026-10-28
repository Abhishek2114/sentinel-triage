"""Prometheus metrics.

Label values are always from small fixed sets (route templates, outcome
names, redaction kinds), never from request data such as service names, so
the number of series stays bounded however many services send logs.

With several processes (uvicorn workers, Celery) set PROMETHEUS_MULTIPROC_DIR
to a directory they share; /metrics then aggregates all of them.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)
from prometheus_client import multiprocess as _multiprocess

HTTP_REQUESTS = Counter(
    "sentinel_http_requests_total", "HTTP requests", ["method", "route", "status"]
)
HTTP_LATENCY = Histogram(
    "sentinel_http_request_duration_seconds",
    "HTTP request latency",
    ["route"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30),
)
INGEST_LINES = Counter("sentinel_ingest_events_total", "Ingested events", ["result"])
INGEST_TRANSFORMS = Counter(
    "sentinel_ingest_transforms_total",
    "Changes made to events at ingest (truncated, multiline_joined, timestamp_clamped)",
    ["kind"],
)
REDACTIONS = Counter("sentinel_redactions_total", "Secrets/PII redacted at ingest", ["kind"])
DETECTION_SECONDS = Histogram(
    "sentinel_detection_duration_seconds",
    "Detection run duration",
    buckets=(0.1, 0.5, 1, 2.5, 5, 10, 30, 60, 120),
)
DETECTION_SKIPPED_SERVICES = Counter(
    "sentinel_detection_skipped_services_total", "Services left out by the per-run cap"
)
ANOMALIES = Counter("sentinel_anomalies_total", "New anomalies", ["detector"])
TRIAGE = Counter("sentinel_triage_total", "Triage outcomes", ["action", "method"])
LLM_CALLS = Counter(
    "sentinel_llm_calls_total", "LLM calls by outcome (ok, error, budget_denied)", ["outcome"]
)
LLM_TOKENS = Counter("sentinel_llm_tokens_total", "LLM tokens used", ["direction"])
WS_CONNECTIONS = Gauge(
    "sentinel_websocket_connections",
    "Open dashboard WebSocket connections",
    multiprocess_mode="livesum",
)


def record_ingest(stored: int, parse_errors: int, stats: Mapping[str, int]) -> None:
    INGEST_LINES.labels("stored").inc(stored)
    INGEST_LINES.labels("parse_error").inc(parse_errors)
    for key, n in stats.items():
        if key.startswith("redacted:"):
            REDACTIONS.labels(key.removeprefix("redacted:")).inc(n)
        else:
            INGEST_TRANSFORMS.labels(key).inc(n)


def render() -> tuple[bytes, str]:
    """The exposition payload, aggregated across processes when configured."""
    if os.environ.get("PROMETHEUS_MULTIPROC_DIR"):
        registry = CollectorRegistry()
        _multiprocess.MultiProcessCollector(registry)  # type: ignore[no-untyped-call]
        return generate_latest(registry), CONTENT_TYPE_LATEST
    return generate_latest(), CONTENT_TYPE_LATEST
