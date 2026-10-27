import type { Anomaly, Incident } from "../api";
import { formatDateTime } from "../format";

interface Props {
  incidents: Incident[];
  anomalies: Anomaly[];
  selectedId: number | null;
  onSelect: (id: number) => void;
}

const HOUR = 3_600_000;

/**
 * One row per service: incident spans as bars, every anomaly as a tick.
 * Shows at a glance how error-burst's few clean alerts compare with Isolation
 * Forest's noise, and how grouped anomalies sit inside one incident.
 */
export function Timeline({ incidents, anomalies, selectedId, onSelect }: Props) {
  const times = [
    ...anomalies.flatMap((a) => [Date.parse(a.window_start), Date.parse(a.window_end)]),
    ...incidents.flatMap((i) => [Date.parse(i.started_at), Date.parse(i.last_seen_at)]),
  ];
  if (times.length === 0) return null;

  const start = Math.floor(Math.min(...times) / HOUR) * HOUR;
  const end = Math.max(Math.ceil(Math.max(...times) / HOUR) * HOUR, start + HOUR);
  const x = (iso: string) => ((Date.parse(iso) - start) / (end - start)) * 100;

  const services = [
    ...new Set([...anomalies, ...incidents].map((x) => x.service ?? "unknown")),
  ].sort();
  const hours = Array.from({ length: (end - start) / HOUR + 1 }, (_, i) => start + i * HOUR);
  const labelEvery = Math.ceil(hours.length / 8);

  return (
    <section className="panel timeline" aria-label="Timeline">
      <header className="panel-header">
        <h2>Timeline</h2>
        <div className="legend" aria-hidden="true">
          <span><i className="swatch incident-open" /> open incident</span>
          <span><i className="swatch incident-resolved" /> resolved</span>
          <span><i className="tick-swatch error_burst" /> error-burst anomaly</span>
          <span><i className="tick-swatch iforest" /> Isolation Forest anomaly</span>
        </div>
      </header>
      <div className="timeline-grid">
        {services.map((service) => (
          <div className="timeline-row" key={service}>
            <div className="timeline-label" title={service}>{service}</div>
            <div className="timeline-track">
              {hours.map((h) => (
                <div
                  key={h}
                  className="gridline"
                  style={{ left: `${((h - start) / (end - start)) * 100}%` }}
                />
              ))}
              {incidents
                .filter((i) => (i.service ?? "unknown") === service)
                .map((i) => (
                  <button
                    key={i.id}
                    type="button"
                    className={`incident-bar ${i.status}${i.id === selectedId ? " selected" : ""}`}
                    style={{
                      left: `${x(i.started_at)}%`,
                      width: `max(6px, ${x(i.last_seen_at) - x(i.started_at)}%)`,
                    }}
                    onClick={() => onSelect(i.id)}
                    title={`Incident #${i.id}: ${formatDateTime(i.started_at)}`}
                    aria-label={`Incident ${i.id} on ${service}`}
                  />
                ))}
              {anomalies
                .filter((a) => (a.service ?? "unknown") === service)
                .map((a) => (
                  <div
                    key={a.id}
                    className={`tick ${a.detector}`}
                    style={{ left: `${x(a.window_start)}%` }}
                    title={`${a.detector} · ${formatDateTime(a.window_start)}`}
                  />
                ))}
            </div>
          </div>
        ))}
        <div className="timeline-row axis">
          <div className="timeline-label" />
          <div className="timeline-track">
            {hours.map((h, i) =>
              i % labelEvery === 0 ? (
                <span
                  key={h}
                  // Every other label is dropped on narrow screens (styles.css)
                  className={`axis-label${(i / labelEvery) % 2 === 1 ? " minor" : ""}`}
                  style={{ left: `${((h - start) / (end - start)) * 100}%` }}
                >
                  {new Date(h).toISOString().slice(11, 16)}
                </span>
              ) : null,
            )}
          </div>
        </div>
      </div>
    </section>
  );
}
