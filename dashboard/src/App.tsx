import { useEffect, useMemo, useState } from "react";

import { api, type Anomaly } from "./api";
import { IncidentDetail } from "./components/IncidentDetail";
import { IncidentList } from "./components/IncidentList";
import { Timeline } from "./components/Timeline";
import { filterIncidents, type StatusFilter } from "./incidents";
import { type Connection, useLiveIncidents } from "./useLiveIncidents";

const CONNECTION_LABEL: Record<Connection, string> = {
  connecting: "Connecting…",
  live: "Live",
  reconnecting: "Reconnecting…",
};

export function App({ openSocket }: { openSocket?: (url: string) => WebSocket }) {
  const { incidents, loaded, error, connection, lastEvent, replace } =
    useLiveIncidents(openSocket);
  const [filter, setFilter] = useState<StatusFilter>("open");
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [anomalies, setAnomalies] = useState<Anomaly[]>([]);
  const [flashId, setFlashId] = useState<number | null>(null);

  // Anomalies feed the timeline; refetch when an incident changes
  useEffect(() => {
    api.anomalies().then(setAnomalies).catch(() => setAnomalies([]));
  }, [lastEvent, incidents.length]);

  useEffect(() => {
    if (!lastEvent) return;
    setFlashId(lastEvent.incident.id);
    const timer = setTimeout(() => setFlashId(null), 2500);
    return () => clearTimeout(timer);
  }, [lastEvent]);

  const visible = useMemo(() => filterIncidents(incidents, filter), [incidents, filter]);
  const counts = useMemo(
    () => ({
      open: filterIncidents(incidents, "open").length,
      resolved: filterIncidents(incidents, "resolved").length,
      all: incidents.length,
    }),
    [incidents],
  );

  // Default to the first incident, then keep it: a new arrival or resolving the
  // shown incident must not swap what someone is reading
  useEffect(() => {
    if (selectedId === null && visible.length > 0) setSelectedId(visible[0].id);
  }, [selectedId, visible]);
  const selected = incidents.find((i) => i.id === selectedId) ?? visible[0] ?? null;

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <span className="logo" aria-hidden="true">◆</span>
          <h1>Sentinel Triage</h1>
        </div>
        <div className={`connection ${connection}`} role="status">
          <span className="dot" aria-hidden="true" /> {CONNECTION_LABEL[connection]}
        </div>
      </header>

      <p className="sr-only" aria-live="polite">
        {lastEvent &&
          `${lastEvent.type === "incident.created" ? "New incident" : "Incident updated"} on ${
            lastEvent.incident.service ?? "all services"
          }`}
      </p>

      {error && <div className="banner error-text">Could not load incidents: {error}</div>}

      {loaded && incidents.length === 0 && !error ? (
        <EmptyState />
      ) : (
        <main className="layout">
          <Timeline
            incidents={incidents}
            anomalies={anomalies}
            selectedId={selected?.id ?? null}
            onSelect={setSelectedId}
          />
          <IncidentList
            incidents={visible}
            filter={filter}
            counts={counts}
            selectedId={selected?.id ?? null}
            flashId={flashId}
            onFilter={setFilter}
            onSelect={setSelectedId}
          />
          {selected ? (
            <IncidentDetail
              incidentId={selected.id}
              version={selected.updated_at}
              onChanged={replace}
            />
          ) : (
            <section className="panel detail muted">Select an incident.</section>
          )}
        </main>
      )}
    </div>
  );
}

function EmptyState() {
  return (
    <main className="empty-state panel">
      <h2>No incidents yet</h2>
      <p className="muted">
        Ingest some logs and run detection. New incidents appear here live.
      </p>
      <pre>{`python scripts/generate_logs.py
curl -X POST localhost:8000/api/v1/ingest -H 'Content-Type: application/json' \\
  -d '{"file_path": "data/synthetic/events.log"}'
curl -X POST localhost:8000/api/v1/runbooks/index
curl -X POST localhost:8000/api/v1/detect -H 'Content-Type: application/json' -d '{}'
curl -X POST localhost:8000/api/v1/anomalies/1/triage`}</pre>
    </main>
  );
}
