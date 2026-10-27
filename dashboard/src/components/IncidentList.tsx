import type { Incident } from "../api";
import { formatDateTime, plural } from "../format";
import type { StatusFilter } from "../incidents";

interface Props {
  incidents: Incident[];
  filter: StatusFilter;
  counts: Record<StatusFilter, number>;
  selectedId: number | null;
  flashId: number | null;
  onFilter: (filter: StatusFilter) => void;
  onSelect: (id: number) => void;
}

const FILTERS: { value: StatusFilter; label: string }[] = [
  { value: "open", label: "Open" },
  { value: "resolved", label: "Resolved" },
  { value: "all", label: "All" },
];

export function IncidentList({
  incidents,
  filter,
  counts,
  selectedId,
  flashId,
  onFilter,
  onSelect,
}: Props) {
  return (
    <section className="panel incident-list" aria-label="Incidents">
      <div className="filters" role="tablist" aria-label="Filter incidents by status">
        {FILTERS.map((f) => (
          <button
            key={f.value}
            type="button"
            role="tab"
            aria-selected={filter === f.value}
            className={filter === f.value ? "active" : ""}
            onClick={() => onFilter(f.value)}
          >
            {f.label} <span className="count">{counts[f.value]}</span>
          </button>
        ))}
      </div>
      {incidents.length === 0 ? (
        <p className="muted empty-list">No {filter === "all" ? "" : `${filter} `}incidents.</p>
      ) : (
        <ul>
          {incidents.map((incident) => (
            <li key={incident.id}>
              <button
                type="button"
                className={[
                  "incident-card",
                  incident.status,
                  incident.id === selectedId ? "selected" : "",
                  incident.id === flashId ? "flash" : "",
                ].join(" ")}
                aria-current={incident.id === selectedId}
                onClick={() => onSelect(incident.id)}
              >
                <div className="card-top">
                  <span className="service">{incident.service ?? "all services"}</span>
                  <ConfidenceBadge value={incident.confidence} />
                </div>
                <div className="card-runbook">{runbookTitle(incident.runbook)}</div>
                <div className="card-meta muted">
                  #{incident.id} · {formatDateTime(incident.started_at)} ·{" "}
                  {plural(incident.anomaly_count, "anomaly", "anomalies")}
                </div>
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

const ACRONYMS = new Set(["api", "cpu", "db", "dns", "http", "oom", "tls"]);

/** "db_connection_failure.md" → "DB Connection Failure" */
export function runbookTitle(runbook: string | null): string {
  if (!runbook) return "No runbook matched";
  return runbook
    .replace(/\.md$/, "")
    .split("_")
    .map((w) => (ACRONYMS.has(w) ? w.toUpperCase() : w.charAt(0).toUpperCase() + w.slice(1)))
    .join(" ");
}

export function ConfidenceBadge({ value }: { value: Incident["confidence"] }) {
  if (!value) return null;
  return <span className={`badge confidence-${value}`}>{value} confidence</span>;
}
