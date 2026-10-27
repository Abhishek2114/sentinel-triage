import type { Incident, IncidentDetail } from "../api";

export function incident(overrides: Partial<Incident> = {}): Incident {
  return {
    id: 1,
    anomaly_id: 10,
    service: "payment-service",
    started_at: "2024-06-15T01:00:00Z",
    last_seen_at: "2024-06-15T01:10:00Z",
    anomaly_count: 1,
    summary: "error_burst flagged payment-service.",
    root_cause: "The log patterns best match the CPU Spike Runbook (cpu_spike.md).",
    confidence: "low",
    runbook: "cpu_spike.md",
    next_steps: ["Immediate: Scale horizontally"],
    evidence: [
      {
        template: "CPU usage at <NUM>%",
        count: 6,
        baseline_count: 0,
        errors: 4,
        lift: 42.9,
        example: "CPU usage at 97%",
        event_ids: [100],
      },
    ],
    related_event_ids: [100],
    triage_method: "fallback",
    llm_model: null,
    status: "open",
    created_at: "2026-10-27T10:00:00Z",
    updated_at: "2026-10-27T10:00:00Z",
    ...overrides,
  };
}

export function detail(base: Incident): IncidentDetail {
  return {
    ...base,
    anomalies: [
      {
        id: base.anomaly_id,
        detector: "error_burst",
        score: 5.5,
        window_start: base.started_at,
        window_end: base.last_seen_at,
        service: base.service,
        incident_id: base.id,
      },
    ],
    related_events: [
      {
        id: 100,
        timestamp: "2024-06-15T01:02:03Z",
        service: base.service ?? "",
        level: "ERROR",
        message: "CPU usage at 97%",
      },
    ],
  };
}
