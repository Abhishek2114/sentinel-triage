// Types mirror app/schemas/__init__.py; the API is proxied under /api/v1.

export type IncidentStatus = "open" | "resolved";

export interface EvidencePattern {
  template: string;
  count: number;
  baseline_count: number;
  errors: number;
  lift: number;
  example: string;
  event_ids: number[];
}

export interface Incident {
  id: number;
  anomaly_id: number;
  service: string | null;
  started_at: string;
  last_seen_at: string;
  anomaly_count: number;
  summary: string;
  root_cause: string | null;
  confidence: "low" | "medium" | "high" | null;
  runbook: string | null;
  next_steps: string[] | null;
  evidence: EvidencePattern[] | null;
  related_event_ids: number[] | null;
  triage_method: "llm" | "fallback";
  llm_model: string | null;
  status: IncidentStatus;
  created_at: string;
  updated_at: string;
}

export interface Anomaly {
  id: number;
  detector: string;
  score: number;
  window_start: string;
  window_end: string;
  service: string | null;
  incident_id: number | null;
}

export interface LogEvent {
  id: number;
  timestamp: string;
  service: string;
  level: string;
  message: string;
}

export interface IncidentDetail extends Incident {
  anomalies: Anomaly[];
  related_events: LogEvent[] | null;
}

export interface IncidentEvent {
  type: "incident.created" | "incident.updated";
  incident: Incident;
}

const BASE = "/api/v1";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${BASE}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (!response.ok) {
    throw new Error(`${init?.method ?? "GET"} ${path} failed: ${response.status}`);
  }
  return (await response.json()) as T;
}

export const api = {
  incidents: () => request<Incident[]>("/incidents?limit=500"),
  incident: (id: number) => request<IncidentDetail>(`/incidents/${id}`),
  anomalies: () => request<Anomaly[]>("/anomalies?limit=1000"),
  setStatus: (id: number, status: IncidentStatus) =>
    request<Incident>(`/incidents/${id}`, {
      method: "PATCH",
      body: JSON.stringify({ status }),
    }),
};

export function alertsUrl(location: Location = window.location): string {
  const scheme = location.protocol === "https:" ? "wss" : "ws";
  return `${scheme}://${location.host}${BASE}/ws/alerts`;
}
