import { useEffect, useRef, useState } from "react";

import {
  ApiError,
  api,
  type DemoRun,
  type IncidentEvent,
  type Overview as OverviewData,
  type ServiceState,
} from "../api";
import { formatAgo, formatDateTime, percent, plural, runbookTitle } from "../format";
import { useCountUp, useFlip, useStopwatch } from "../motion";
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
  lastEvent: IncidentEvent | null;
}

/** Waiting longer than this for the demo's incident means something is off */
const DEMO_PATIENCE_S = 45;

interface DemoState {
  busy: boolean;
  startedAt?: number;
  result?: DemoRun;
  error?: string;
  incidentId?: number;
  finishedAt?: number;
}

function CountUp({ value }: { value: number }) {
  return <>{useCountUp(value).toLocaleString()}</>;
}

export function Overview(props: Props) {
  const { data, error, canRunDemo, demoMode, onOpenIncident, onShowLogs, onDemoStarted, lastEvent } = props;
  const [demo, setDemo] = useState<DemoState>({ busy: false });
  const boardRef = useRef<HTMLUListElement>(null);
  useFlip(boardRef, [data?.services.map((s) => s.service).join()]);

  const runDemo = async () => {
    setDemo({ busy: true, startedAt: Date.now() });
    try {
      const result = await api.runDemo();
      setDemo((d) => ({ ...d, busy: false, result }));
      onDemoStarted();
    } catch (e) {
      setDemo({ busy: false, error: e instanceof ApiError ? e.message : "Could not start the demo" });
    }
  };

  // The demo's incident arrives over the WebSocket, often before the POST returns
  useEffect(() => {
    const i = lastEvent?.incident;
    if (!i || !i.service?.startsWith("demo-")) return;
    setDemo((d) =>
      d.startedAt && !d.incidentId && !d.error ? { ...d, incidentId: i.id, finishedAt: Date.now() } : d,
    );
  }, [lastEvent]);

  if (!data) {
    return error ? (
      <section className="panel">
        <p className="error">{error}</p>
      </section>
    ) : (
      <section className="skeleton" aria-busy="true">
        <p className="visually-hidden">Loading…</p>
        <div className="bone bone-verdict" />
        <div className="bone-row">
          {[0, 1, 2].map((n) => (
            <div key={n} className="bone bone-card" />
          ))}
        </div>
      </section>
    );
  }

  const v = data.verdict;
  return (
    <>
      <section key={v.level} className={`verdict verdict-${v.level}`} aria-labelledby="verdict-headline" data-tilt="soft">
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
        <section className="panel demo" aria-labelledby="demo-title" data-tilt="soft">
          <div className="demo-body">
            <h2 id="demo-title">See it work</h2>
            <p className="muted">
              Generates an hour of <strong>synthetic</strong> logs for five <code>demo-</code> services with one
              injected fault, then runs detection and triage on them exactly as on real logs.
            </p>
            {demo.result && <p role="status">{demo.result.detail}</p>}
            {demo.error && <p role="alert" className="error">{demo.error}</p>}
            {demo.startedAt && !demo.error && <Pipeline demo={demo} onOpenIncident={onOpenIncident} />}
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
          <ul ref={boardRef} className="board" aria-labelledby="board-title">
            {data.services.map((s, n) => {
              const incident = data.open_incidents.find((i) => i.id === s.open_incident_id);
              return (
                <li
                  key={s.service}
                  data-flip={s.service}
                  className={`card state-${s.status}`}
                  data-tilt
                  style={{ "--i": n } as React.CSSProperties}
                >
                  <div className="card-head">
                    <h3 title={s.service}>{s.service}</h3>
                    <span className={`badge badge-${s.status}`}>{STATE_LABEL[s.status]}</span>
                  </div>
                  <Sparkline errors={s.errors_per_minute} events={s.events_per_minute} label={s.service} />
                  <dl className="stats">
                    <div>
                      <dt>Events</dt>
                      <dd>
                        <CountUp value={s.events} />
                      </dd>
                    </div>
                    <div>
                      <dt>Errors</dt>
                      <dd>
                        <CountUp value={s.errors} /> <span className="muted">({percent(s.error_rate)})</span>
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

const STAGES = [
  { label: "Generate", sub: "synthetic logs, one fault" },
  { label: "Ingest", sub: "parse, template, redact" },
  { label: "Detect + triage", sub: "error burst, evidence, runbook" },
  { label: "Incident", sub: "pushed live over WebSocket" },
] as const;

/** The demo run as it happens: each stage lights up as the server reaches it. */
function Pipeline({ demo, onOpenIncident }: { demo: DemoState; onOpenIncident: (id: number) => void }) {
  const done = demo.incidentId !== undefined;
  const running = !done;
  const elapsed = useStopwatch(demo.startedAt ?? null, running);
  const total = done && demo.finishedAt && demo.startedAt ? (demo.finishedAt - demo.startedAt) / 1000 : elapsed;
  const lines = useCountUp(demo.result?.lines ?? 0, 900);
  // Stages 1-2 finish when the POST returns; 3 runs until the incident event
  const reached = done ? 4 : demo.result ? 2 : 0;
  const sub = (n: number) =>
    n === 0 && demo.result
      ? `${lines.toLocaleString()} lines · ${demo.result.services.length} services`
      : n === 3 && done
        ? `#${demo.incidentId} opened`
        : STAGES[n].sub;

  return (
    <>
      <div className="pipeline-track" style={{ "--progress": Math.min(reached, 3) / 3 } as React.CSSProperties}>
        <span className="pipeline-fill" aria-hidden="true" />
        <ol className="pipeline" aria-label="Demo progress">
          {STAGES.map((stage, n) => {
            const state = n < reached ? "done" : n === reached && running ? "active" : "pending";
            return (
              <li key={stage.label} className={`stage stage-${state}`}>
                <span className="stage-node" aria-hidden="true">
                  {state === "done" ? "✓" : n + 1}
                </span>
                <span className="stage-text">
                  <span className="stage-label">
                    {stage.label}
                    <span className="visually-hidden">: {state}</span>
                  </span>
                  <span className="stage-sub">{sub(n)}</span>
                </span>
              </li>
            );
          })}
        </ol>
      </div>
      {/* The stopwatch ticks every 100 ms, so only the outcome is announced */}
      <p className="visually-hidden" role="status">
        {done ? `Incident #${demo.incidentId} raised.` : "Demo running."}
      </p>
      <p className="pipeline-foot muted">
        {done ? (
          <>
            Incident #{demo.incidentId} raised {total.toFixed(1)}s after the click.{" "}
            <button type="button" className="link" onClick={() => onOpenIncident(demo.incidentId!)}>
              Open it
            </button>
          </>
        ) : elapsed > DEMO_PATIENCE_S ? (
          "Still waiting for the incident; it may already be under Incidents."
        ) : (
          `Running… ${elapsed.toFixed(1)}s`
        )}
      </p>
    </>
  );
}
