import { useEffect, useState } from "react";

import { api, type Incident, type IncidentDetail as Detail } from "../api";
import { formatDateTime, formatDuration, formatTime, plural } from "../format";
import { ConfidenceBadge, runbookTitle } from "./IncidentList";

interface Props {
  incidentId: number;
  /** Changes when a live event updates this incident, to trigger a refetch. */
  version: string;
  onChanged: (incident: Incident) => void;
}

export function IncidentDetail({ incidentId, version, onChanged }: Props) {
  const [detail, setDetail] = useState<Detail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let cancelled = false;
    api
      .incident(incidentId)
      .then((d) => {
        if (!cancelled) {
          setDetail(d);
          setError(null);
        }
      })
      .catch((e: unknown) => !cancelled && setError(String(e)));
    return () => {
      cancelled = true;
    };
  }, [incidentId, version]);

  if (error) return <section className="panel detail error-text">{error}</section>;
  if (!detail || detail.id !== incidentId) {
    return <section className="panel detail muted">Loading incident…</section>;
  }

  const toggleStatus = async () => {
    setSaving(true);
    try {
      const updated = await api.setStatus(
        detail.id,
        detail.status === "open" ? "resolved" : "open",
      );
      setDetail({ ...detail, ...updated });
      onChanged(updated);
    } catch (e) {
      setError(String(e));
    } finally {
      setSaving(false);
    }
  };

  const maxLift = Math.max(1, ...(detail.evidence ?? []).map((p) => p.lift));

  return (
    <section className="panel detail" aria-label={`Incident ${detail.id}`}>
      <header className="detail-header">
        <div>
          <div className="eyebrow">
            Incident #{detail.id} · <span className={`status ${detail.status}`}>{detail.status}</span>
          </div>
          <h2>
            {detail.service ?? "All services"} <span className="muted">·</span>{" "}
            {runbookTitle(detail.runbook)}
          </h2>
          <div className="meta-row">
            <ConfidenceBadge value={detail.confidence} />
            <span className="badge method">
              {detail.triage_method === "llm" ? `LLM · ${detail.llm_model}` : "Rule-based report"}
            </span>
            <span className="muted">
              {formatDateTime(detail.started_at)} ·{" "}
              {formatDuration(detail.started_at, detail.last_seen_at)} ·{" "}
              {plural(detail.anomaly_count, "anomaly", "anomalies")}
            </span>
          </div>
        </div>
        <button type="button" className="primary" onClick={toggleStatus} disabled={saving}>
          {detail.status === "open" ? "Resolve" : "Reopen"}
        </button>
      </header>

      <div className="detail-body">
        <div className="section">
          <h3>Summary</h3>
          <p>{detail.summary}</p>
          {detail.root_cause && (
            <>
              <h3>Likely cause</h3>
              <p>{detail.root_cause}</p>
            </>
          )}
        </div>

        {detail.next_steps && detail.next_steps.length > 0 && (
          <div className="section">
            <h3>Next steps{detail.runbook && <span className="muted"> · from {detail.runbook}</span>}</h3>
            <ol className="steps">
              {detail.next_steps.map((step) => (
                <li key={step}>{step}</li>
              ))}
            </ol>
          </div>
        )}

        {detail.evidence && detail.evidence.length > 0 && (
          <div className="section">
            <h3>Evidence · log patterns that changed</h3>
            <table className="evidence">
              <thead>
                <tr>
                  <th scope="col">Pattern</th>
                  <th scope="col" className="num">Now</th>
                  <th scope="col" className="num">Hour before</th>
                  <th scope="col" className="num">Errors</th>
                  <th scope="col">Lift</th>
                </tr>
              </thead>
              <tbody>
                {detail.evidence.map((p) => (
                  <tr key={p.template}>
                    <td>
                      <code>{p.template}</code>
                      {p.baseline_count === 0 && <span className="badge new">new</span>}
                    </td>
                    <td className="num">{p.count}</td>
                    <td className="num">{p.baseline_count}</td>
                    <td className="num">{p.errors}</td>
                    <td className="lift-cell">
                      <span className="lift-bar" style={{ width: `${(p.lift / maxLift) * 100}%` }} />
                      <span className="lift-value">{p.lift.toFixed(1)}×</span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {detail.related_events && detail.related_events.length > 0 && (
          <div className="section">
            <h3>Log lines</h3>
            <ul className="log-lines">
              {detail.related_events.map((e) => (
                <li key={e.id}>
                  <span className="muted">{formatTime(e.timestamp)}</span>{" "}
                  <span className={`level ${e.level.toLowerCase()}`}>{e.level}</span>{" "}
                  <code>{e.message}</code>
                </li>
              ))}
            </ul>
          </div>
        )}

        <div className="section">
          <h3>Grouped anomalies</h3>
          <ul className="anomaly-list">
            {detail.anomalies.map((a) => (
              <li key={a.id}>
                <span className="muted">#{a.id}</span> {a.detector}{" "}
                <span className="muted">
                  {formatTime(a.window_start)}–{formatTime(a.window_end)} · score {a.score.toFixed(1)}
                </span>
              </li>
            ))}
          </ul>
        </div>
      </div>
    </section>
  );
}
