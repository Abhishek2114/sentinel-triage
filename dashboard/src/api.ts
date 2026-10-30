// Types mirror app/schemas/__init__.py. The API is served from the same origin.

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
  resolution?: string | null;
  resolved_at?: string | null;
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

export interface SimilarIncident {
  id: number;
  service: string | null;
  summary: string;
  runbook: string | null;
  resolution: string | null;
  resolved_at: string | null;
  score: number;
}

export interface IncidentDetail extends Incident {
  anomalies: Anomaly[];
  related_events: LogEvent[] | null;
  similar_incidents?: SimilarIncident[];
}

export interface IncidentEvent {
  type: "incident.created" | "incident.updated";
  incident: Incident;
}

export type ServiceState = "incident" | "elevated" | "healthy" | "quiet";

export interface ServiceHealth {
  service: string;
  status: ServiceState;
  events: number;
  errors: number;
  error_rate: number;
  last_seen: string | null;
  open_incident_id: number | null;
  errors_per_minute: number[];
  events_per_minute: number[];
}

export interface Overview {
  verdict: { level: "incident" | "warning" | "ok" | "no_data"; headline: string; detail: string };
  as_of: string;
  anchored: boolean;
  window_minutes: number;
  services: ServiceHealth[];
  services_truncated: boolean;
  open_incidents: Incident[];
}

export interface PublicConfig {
  version: string;
  demo_mode: boolean;
  read_requires_key: boolean;
  write_requires_key: boolean;
  llm_provider: string | null;
  llm_calls_today: number;
  llm_calls_limit: number;
}

export interface DemoRun {
  status: "started";
  lines: number;
  services: string[];
  fault: string;
  detail: string;
}

export interface EventQuery {
  service?: string;
  level?: string;
  q?: string;
  after_id?: number;
  limit?: number;
}

const BASE = "/api/v1";
const KEY_STORAGE = "sentinel.apiKey";

// The key lives in sessionStorage: gone when the tab closes, never in a URL.
export const apiKey = {
  get(): string | null {
    try {
      return sessionStorage.getItem(KEY_STORAGE);
    } catch {
      return null;
    }
  },
  set(key: string | null) {
    try {
      if (key) sessionStorage.setItem(KEY_STORAGE, key);
      else sessionStorage.removeItem(KEY_STORAGE);
    } catch {
      // storage blocked: the key just won't survive a reload
    }
  },
};

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const key = apiKey.get();
  const response = await fetch(`${BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(key ? { Authorization: `Bearer ${key}` } : {}),
      ...init?.headers,
    },
  });
  if (!response.ok) {
    let detail = `${response.status}`;
    try {
      const body = (await response.json()) as { detail?: unknown };
      if (typeof body.detail === "string") detail = body.detail;
    } catch {
      // not JSON
    }
    throw new ApiError(response.status, detail);
  }
  return (await response.json()) as T;
}

function query(params: object): string {
  const entries = Object.entries(params).filter(([, v]) => v !== undefined && v !== "");
  return entries.length ? `?${new URLSearchParams(entries.map(([k, v]) => [k, String(v)]))}` : "";
}

export const api = {
  config: () => request<PublicConfig>("/config"),
  /** 200 if ``key`` may make changes, else an ApiError with status 401. */
  authCheck: (key: string) =>
    request<{ ok: boolean }>("/auth/check", { headers: { Authorization: `Bearer ${key}` } }),
  overview: (windowMinutes = 30) => request<Overview>(`/overview?window_minutes=${windowMinutes}`),
  incidents: () => request<Incident[]>("/incidents?limit=500"),
  incident: (id: number) => request<IncidentDetail>(`/incidents/${id}`),
  anomalies: () => request<Anomaly[]>("/anomalies?limit=1000"),
  setStatus: (id: number, status: IncidentStatus, resolution?: string) =>
    request<Incident>(`/incidents/${id}`, {
      method: "PATCH",
      body: JSON.stringify(resolution === undefined ? { status } : { status, resolution }),
    }),
  events: (params: EventQuery = {}) => request<LogEvent[]>(`/events${query(params)}`),
  context: (id: number) => request<LogEvent[]>(`/events/${id}/context?before=15&after=15`),
  runbook: (name: string) =>
    request<{ name: string; markdown: string }>(`/runbooks/${encodeURIComponent(name)}`),
  runDemo: () => request<DemoRun>("/demo/run", { method: "POST" }),
};

export function alertsUrl(location: Location = window.location): string {
  const scheme = location.protocol === "https:" ? "wss" : "ws";
  return `${scheme}://${location.host}${BASE}/ws/alerts`;
}
