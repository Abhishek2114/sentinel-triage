import { type KeyboardEvent, useEffect, useMemo, useRef, useState } from "react";

import { ApiError, api, type Incident, type IncidentDetail as Detail } from "../api";
import { formatAgo, formatDateTime, formatDuration, formatTime, plural, runbookTitle } from "../format";
import { filterIncidents, type StatusFilter } from "../incidents";
import { useFlip } from "../motion";
import { Dialog } from "./Dialog";
import { Markdown } from "./Markdown";

interface Props {
  incidents: Incident[];
  loaded: boolean;
  selectedId: number | null;
  canWrite: boolean;
  onSelect: (id: number | null) => void;
  onChanged: (incident: Incident) => void;
  onNeedKey: () => void;
  onShowContext: (eventId: number) => void;
  searchRef: React.RefObject<HTMLInputElement>;
}

export function Incidents(props: Props) {
  const { incidents, loaded, selectedId, onSelect, searchRef } = props;
  const [status, setStatus] = useState<StatusFilter>("open");
  const [text, setText] = useState("");
  const listRef = useRef<HTMLUListElement>(null);
  // Re-sorted rows glide to their new place; arrivals slide in and glow

  const visible = useMemo(() => {
    const needle = text.trim().toLowerCase();
    return filterIncidents(incidents, status).filter(
      (i) =>
        !needle ||
        `${i.service ?? ""} ${i.summary} ${i.runbook ?? ""} #${i.id}`.toLowerCase().includes(needle),
    );
  }, [incidents, status, text]);

  // j / k move through the list, Enter opens (handled on the list itself)
  const onListKey = (e: KeyboardEvent<HTMLUListElement>) => {
    const index = visible.findIndex((i) => i.id === selectedId);
    if (e.key === "j" || e.key === "ArrowDown") {
      e.preventDefault();
      const next = visible[Math.min(index + 1, visible.length - 1)];
      if (next) onSelect(next.id);
    } else if (e.key === "k" || e.key === "ArrowUp") {
      e.preventDefault();
      const prev = visible[Math.max(index - 1, 0)];
      if (prev) onSelect(prev.id);
    }
  };
  useFlip(listRef, [visible.map((i) => `${i.id}:${i.updated_at}`).join()]);
  useEffect(() => {
    listRef.current?.querySelector<HTMLElement>('[aria-selected="true"]')?.focus({ preventScroll: false });
  }, [selectedId]);

  const counts = {
    open: incidents.filter((i) => i.status === "open").length,
    resolved: incidents.filter((i) => i.status === "resolved").length,
    all: incidents.length,
  };

  return (
    <div className="master-detail">
      <section className="master" aria-labelledby="incidents-title">
        <h1 id="incidents-title" className="visually-hidden">
          Incidents
        </h1>
        <div className="filters">
          <div className="segmented" role="group" aria-label="Status">
            {(["open", "resolved", "all"] as const).map((s) => (
              <button
                key={s}
                type="button"
                aria-pressed={status === s}
                onClick={() => setStatus(s)}
              >
                {s[0].toUpperCase() + s.slice(1)} <span className="count">{counts[s]}</span>
              </button>
            ))}
          </div>
          <label className="search">
            <span className="visually-hidden">Filter incidents</span>
            <input
              ref={searchRef}
              type="search"
              placeholder="Filter by service, cause, #id  ( / )"
              value={text}
              onChange={(e) => setText(e.target.value)}
            />
          </label>
        </div>
        {!loaded ? (
          <p className="muted pad">Loading…</p>
        ) : visible.length === 0 ? (
          <p className="muted pad">
            {status === "open" && !text ? "No open incidents. Nothing needs attention." : "No incidents match."}
          </p>
        ) : (
          <ul
            ref={listRef}
            className="incident-list"
            role="listbox"
            aria-label="Incidents (j and k to move)"
            onKeyDown={onListKey}
          >
            {visible.map((i) => (
              <li
                key={i.id}
                data-flip={i.id}
                role="option"
                aria-selected={i.id === selectedId}
                tabIndex={i.id === selectedId || (selectedId === null && i === visible[0]) ? 0 : -1}
                className={`incident-row status-${i.status}`}
                onClick={() => onSelect(i.id)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    onSelect(i.id);
                  }
                }}
              >
                <div className="row-top">
                  <span className={`dot dot-${i.status}`} aria-hidden="true" />
                  <strong>{i.service ?? "all services"}</strong>
                  <span className="muted">#{i.id}</span>
                  <time className="muted right" dateTime={i.last_seen_at}>
                    {formatAgo(i.last_seen_at)}
                  </time>
                </div>
                <div className="row-cause">{runbookTitle(i.runbook) ?? "Cause unknown"}</div>
                <div className="row-meta muted">
                  {i.status === "open" ? "Open" : "Resolved"} · {formatDuration(i.started_at, i.last_seen_at)}
                  {i.anomaly_count > 1 && ` · ${plural(i.anomaly_count, "anomaly", "anomalies")}`}
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>
      <section className="detail" aria-live="polite">
        {selectedId === null ? (
          <div className="empty-detail muted">
            <p>Select an incident to see what happened, why, and what to do.</p>
            <p>
              <kbd>j</kbd> / <kbd>k</kbd> to move, <kbd>/</kbd> to filter, <kbd>?</kbd> for all shortcuts.
            </p>
          </div>
        ) : (
          <IncidentDetail key={selectedId} id={selectedId} {...props} />
        )}
      </section>
    </div>
  );
}

function IncidentDetail({
  id,
  canWrite,
  onChanged,
  onNeedKey,
  onShowContext,
  onSelect,
}: Props & { id: number }) {
  const [detail, setDetail] = useState<Detail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const [saving, setSaving] = useState(false);
  const [runbook, setRunbook] = useState<string | null>(null);
  const [done, setDone] = useState<Set<number>>(new Set());
  const headingRef = useRef<HTMLHeadingElement>(null);

  useEffect(() => {
    let live = true;
    api
      .incident(id)
      .then((d) => live && (setDetail(d), setNote(d.resolution ?? "")))
      .catch((e: unknown) => live && setError(e instanceof Error ? e.message : String(e)));
    return () => {
      live = false;
    };
  }, [id]);
  useEffect(() => headingRef.current?.focus(), [detail?.id]);

  if (error) return <p className="error pad">Could not load incident #{id}: {error}</p>;
  if (!detail) return <p className="muted pad">Loading incident #{id}…</p>;

  const save = async (status: "open" | "resolved") => {
    setSaving(true);
    try {
      const updated = await api.setStatus(detail.id, status, status === "resolved" ? note : undefined);
      setDetail({ ...detail, ...updated });
      onChanged(updated);
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) onNeedKey();
      else setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };
  const openRunbook = async () => {
    if (!detail.runbook) return;
    try {
      setRunbook((await api.runbook(detail.runbook)).markdown);
    } catch {
      setRunbook("Runbook not available.");
    }
  };

  const steps = detail.next_steps ?? [];
  // Log scale: one ×40 pattern shouldn't flatten every other bar
  const maxLift = Math.max(...(detail.evidence ?? []).map((e) => Math.log1p(e.lift)), 1e-9);
  const method =
    detail.triage_method === "llm"
      ? `Written by ${detail.llm_model ?? "an LLM"} from the evidence below; claims checked against it`
      : "Rule-based report (no LLM used): runbook chosen by retrieval from the log patterns";

  return (
    <article className="incident" aria-labelledby="incident-title">
      <header className="incident-head">
        <div>
          <p className="eyebrow">
            Incident #{detail.id} · {detail.status === "open" ? "Open" : "Resolved"}
          </p>
          <h2 id="incident-title" ref={headingRef} tabIndex={-1}>
            {detail.service ?? "All services"}: {runbookTitle(detail.runbook) ?? "unexplained anomaly"}
          </h2>
          <p className="muted">
            {formatDateTime(detail.started_at)} → {formatTime(detail.last_seen_at)} UTC (
            {formatDuration(detail.started_at, detail.last_seen_at)})
            {detail.anomaly_count > 1 && ` · ${plural(detail.anomaly_count, "anomaly", "anomalies")} grouped`}
          </p>
        </div>
        <button type="button" className="icon-button only-narrow" onClick={() => onSelect(null)} aria-label="Back to list">
          ←
        </button>
      </header>

      <section className="triage" aria-label="Triage">
        <div className="triage-block">
          <h3>What happened</h3>
          <p>{detail.summary}</p>
        </div>
        <div className="triage-block">
          <h3>
            Likely cause{" "}
            {detail.confidence && (
              <span className={`badge conf-${detail.confidence}`}>{detail.confidence} confidence</span>
            )}
          </h3>
          <p>{detail.root_cause ?? "Unknown."}</p>
          <p className="fine">{method}</p>
        </div>
        {steps.length > 0 && (
          <div className="triage-block">
            <h3>Next steps</h3>
            <ol className="steps">
              {steps.map((s, n) => (
                <li key={n}>
                  <label>
                    <input
                      type="checkbox"
                      checked={done.has(n)}
                      onChange={() =>
                        setDone((d) => {
                          const next = new Set(d);
                          if (next.has(n)) next.delete(n);
                          else next.add(n);
                          return next;
                        })
                      }
                    />
                    <span>{s}</span>
                  </label>
                </li>
              ))}
            </ol>
            {detail.runbook && (
              <button type="button" className="button ghost" onClick={openRunbook}>
                Open runbook: {detail.runbook}
              </button>
            )}
          </div>
        )}
      </section>

      {(detail.similar_incidents?.length ?? 0) > 0 && (
        <section aria-labelledby="similar-title" className="similar">
          <h3 id="similar-title">Seen before</h3>
          <p className="fine">Resolved incidents with matching log patterns, and what fixed them.</p>
          <ul>
            {detail.similar_incidents!.map((s) => (
              <li key={s.id}>
                <button type="button" className="link" onClick={() => onSelect(s.id)}>
                  #{s.id} {s.service ?? ""}: {runbookTitle(s.runbook) ?? "unknown cause"}
                </button>
                {s.resolved_at && <span className="muted"> · resolved {formatAgo(s.resolved_at)}</span>}
                <p className="fix">{s.resolution ? `Fix: ${s.resolution}` : "No fix was recorded."}</p>
              </li>
            ))}
          </ul>
        </section>
      )}

      {detail.evidence && detail.evidence.length > 0 && (
        <section aria-labelledby="evidence-title">
          <h3 id="evidence-title">Evidence: log patterns that changed</h3>
          <div className="table-wrap">
            <table>
              <caption className="visually-hidden">
                Log patterns ranked by how much more often they appeared than in the hour before
              </caption>
              <thead>
                <tr>
                  <th scope="col">Pattern</th>
                  <th scope="col" className="num">Now</th>
                  <th scope="col" className="num">Hour before</th>
                  <th scope="col" className="num">Errors</th>
                  <th scope="col" className="num">Lift</th>
                </tr>
              </thead>
              <tbody>
                {detail.evidence.map((e, n) => (
                  <tr key={n}>
                    <td>
                      <code className="pattern">{e.template}</code>
                      {e.baseline_count === 0 && <span className="badge badge-new">new</span>}
                    </td>
                    <td className="num">{e.count}</td>
                    <td className="num">{e.baseline_count}</td>
                    <td className="num">{e.errors}</td>
                    <td className="num">
                      <span className="lift">
                        <span
                          className="lift-bar"
                          aria-hidden="true"
                          style={{ width: `${(Math.log1p(e.lift) / maxLift) * 90}px` }}
                        />
                        ×{e.lift.toFixed(1)}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {detail.related_events && detail.related_events.length > 0 && (
        <section aria-labelledby="lines-title">
          <h3 id="lines-title">Log lines cited</h3>
          <ul className="loglines">
            {detail.related_events.map((e) => (
              <li key={e.id} className={`lvl-${e.level.toLowerCase()}`}>
                <time dateTime={e.timestamp}>{formatTime(e.timestamp)}</time>
                <span className="lvl">{e.level}</span>
                <span className="msg">{e.message}</span>
                <button type="button" className="link" onClick={() => onShowContext(e.id)}>
                  context<span className="visually-hidden"> around this line</span>
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}

      <section className="resolve" aria-labelledby="resolve-title">
        <h3 id="resolve-title">{detail.status === "open" ? "Resolve" : "Resolution"}</h3>
        {canWrite ? (
          <>
            <label htmlFor="resolution">What fixed it? Incident memory shows this next time.</label>
            <textarea
              id="resolution"
              rows={3}
              maxLength={4000}
              value={note}
              onChange={(e) => setNote(e.target.value)}
              placeholder="e.g. Rolled back v2.14; connection pool size was set to 5 by the new config"
            />
            <div className="row-gap">
              {detail.status === "open" ? (
                <button type="button" className="button primary" disabled={saving} onClick={() => save("resolved")}>
                  {saving ? "Saving…" : "Resolve incident"}
                </button>
              ) : (
                <>
                  <button type="button" className="button" disabled={saving} onClick={() => save("resolved")}>
                    Save note
                  </button>
                  <button type="button" className="button ghost" disabled={saving} onClick={() => save("open")}>
                    Reopen
                  </button>
                </>
              )}
            </div>
          </>
        ) : (
          <p className="muted">
            {detail.resolution ? `Fix: ${detail.resolution}` : "Read-only view."}{" "}
            <button type="button" className="link" onClick={onNeedKey}>
              Sign in as admin
            </button>{" "}
            to resolve incidents.
          </p>
        )}
      </section>

      <Dialog open={runbook !== null} title={detail.runbook ?? "Runbook"} onClose={() => setRunbook(null)}>
        <Markdown source={runbook ?? ""} />
      </Dialog>
    </article>
  );
}
