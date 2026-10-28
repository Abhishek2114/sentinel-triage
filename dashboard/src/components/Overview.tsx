import { useState } from "react";

import { ApiError, api, type DemoRun, type Overview as OverviewData, type ServiceState } from "../api";
import { formatAgo, formatDateTime, percent, plural, runbookTitle } from "../format";
import { Sparkline } from "./Sparkline";

const STATE_LABEL: Record<ServiceState, string> = {
  incident: "Incident",
  elevated: "Elevated errors",
  healthy: "Healthy",
  quiet: "No recent logs",
};

interface Props {
  data: OverviewData | null;
  error: string | null;
  canRunDemo: boolean;
  demoMode: boolean;
  onOpenIncident: (id: number) => void;
  onShowLogs: (service: string) => void;
  onDemoStarted: () => void;
}

export function Overview({ data, error, canRunDemo, demoMode, onOpenIncident, onShowLogs, onDemoStarted }: Props) {
  const [demo, setDemo] = useState<{ busy: boolean; result?: DemoRun; error?: string }>({ busy: false });

  const runDemo = async () => {
    setDemo({ busy: true });
    try {
      const result = await api.runDemo();
      setDemo({ busy: false, result });
      onDemoStarted();
    } catch (e) {
      setDemo({ busy: false, error: e instanceof ApiError ? e.message : "Could not start the demo" });
    }
  };

  if (!data) {
    return (
      <section className="panel" aria-busy={!error}>
        <p className={error ? "error" : "muted"}>{error ?? "Loading…"}</p>
      </section>
    );
  }

  const v = data.verdict;
  return (
    <>
      <section className={`verdict verdict-${v.level}`} aria-labelledby="verdict-headline">
        <div className="verdict-icon" aria-hidden="true">
          {v.level === "incident" ? "!" : v.level === "warning" ? "▲" : v.level === "ok" ? "✓" : "…"}
        </div>
        <div className="verdict-text">
          <h1 id="verdict-headline">{v.headline}</h1>
          <p>{v.detail}</p>
          <p className="verdict-meta">
            Last {data.window_minutes} minutes, as of {formatDateTime(data.as_of)}
            {data.anchored && " · no recent logs, showing the newest data stored (replayed or historical logs)"}
          </p>
        </div>
        {data.open_incidents[0] && (
          <button type="button" className="button primary" onClick={() => onOpenIncident(data.open_incidents[0].id)}>
            Open incident #{data.open_incidents[0].id}
          </button>
        )}
      </section>

      {(canRunDemo || demoMode) && (
        <section className="panel demo" aria-labelledby="demo-title">
          <div>
            <h2 id="demo-title">See it work</h2>
            <p className="muted">
              Generates an hour of <strong>synthetic</strong> logs for five <code>demo-</code> services with one
              injected fault, then runs detection and triage on them exactly as on real logs.
            </p>
            {demo.result && <p role="status">{demo.result.detail}</p>}
            {demo.error && <p role="alert" className="error">{demo.error}</p>}
          </div>
          <button type="button" className="button primary" onClick={runDemo} disabled={demo.busy || !canRunDemo}>
            {demo.busy ? "Starting…" : "Run demo"}
          </button>
        </section>
      )}

      <section aria-labelledby="board-title">
        <div className="section-head">
          <h2 id="board-title">Services</h2>
          <p className="muted">
            {plural(data.services.length, "service")}, bars are events per minute with errors in red
            {data.services_truncated && " (showing the 60 most affected)"}
          </p>
        </div>
        {data.services.length === 0 ? (
          <p className="panel muted">No logs yet. Use “Ship logs” to send some, or run the demo.</p>
        ) : (
          <ul className="board" aria-labelledby="board-title">
            {data.services.map((s) => {
              const incident = data.open_incidents.find((i) => i.id === s.open_incident_id);
              return (
                <li key={s.service} className={`card state-${s.status}`}>
                  <div className="card-head">
                    <h3 title={s.service}>{s.service}</h3>
                    <span className={`badge badge-${s.status}`}>{STATE_LABEL[s.status]}</span>
                  </div>
                  <Sparkline errors={s.errors_per_minute} events={s.events_per_minute} label={s.service} />
                  <dl className="stats">
                    <div>
                      <dt>Events</dt>
                      <dd>{s.events.toLocaleString()}</dd>
                    </div>
                    <div>
                      <dt>Errors</dt>
                      <dd>
                        {s.errors.toLocaleString()} <span className="muted">({percent(s.error_rate)})</span>
                      </dd>
                    </div>
                    <div>
                      <dt>Last log</dt>
                      <dd>{s.last_seen ? formatAgo(s.last_seen) : "—"}</dd>
                    </div>
                  </dl>
                  <div className="card-actions">
                    {incident && (
                      <button type="button" className="button danger" onClick={() => onOpenIncident(incident.id)}>
                        Incident #{incident.id}
                        {incident.runbook && `: ${runbookTitle(incident.runbook)}`}
                      </button>
                    )}
                    <button type="button" className="button ghost" onClick={() => onShowLogs(s.service)}>
                      Logs<span className="visually-hidden"> for {s.service}</span>
                    </button>
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </section>
    </>
  );
}
