import { useCallback, useEffect, useRef, useState } from "react";

import { api, type LogEvent } from "../api";
import { formatDateTime, formatTime } from "../format";

const MAX_ROWS = 500;
const POLL_MS = 3000;
const LEVELS = ["", "CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"];

interface Props {
  services: string[];
  initialService: string;
  contextId: number | null;
  onContext: (id: number | null) => void;
  paused: boolean;
  onPausedChange: (paused: boolean) => void;
  searchRef: React.RefObject<HTMLInputElement>;
}

export function Logs({ services, initialService, contextId, onContext, paused, onPausedChange, searchRef }: Props) {
  const [service, setService] = useState(initialService);
  const [level, setLevel] = useState("");
  const [text, setText] = useState("");
  const [q, setQ] = useState("");
  const [rows, setRows] = useState<LogEvent[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [newWhilePaused, setNewWhilePaused] = useState(0);
  const newest = useRef(0);

  useEffect(() => setService(initialService), [initialService]);
  // Search as you type, without a request per keystroke
  useEffect(() => {
    const t = setTimeout(() => setQ(text.trim()), 300);
    return () => clearTimeout(t);
  }, [text]);

  const load = useCallback(async () => {
    try {
      const fetched = await api.events({ service, level, q, limit: 200 });
      newest.current = fetched.reduce((m, e) => Math.max(m, e.id), 0);
      setRows(fetched);
      setNewWhilePaused(0);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [service, level, q]);

  useEffect(() => {
    void load();
  }, [load]);

  // Live tail: fetch only lines newer than the newest shown
  useEffect(() => {
    const timer = setInterval(async () => {
      try {
        const fresh = await api.events({ service, level, q, after_id: newest.current, limit: 200 });
        if (!fresh.length) return;
        newest.current = Math.max(newest.current, ...fresh.map((e) => e.id));
        if (paused) setNewWhilePaused((n) => n + fresh.length);
        else setRows((current) => [...fresh, ...current].slice(0, MAX_ROWS));
      } catch {
        // keep the last good view; the next tick retries
      }
    }, POLL_MS);
    return () => clearInterval(timer);
  }, [service, level, q, paused]);

  const resume = () => {
    onPausedChange(false);
    void load();
  };

  return (
    <div className={`logs ${contextId !== null ? "with-context" : ""}`}>
      <section className="logs-main" aria-labelledby="logs-title">
        <div className="filters">
          <h1 id="logs-title" className="visually-hidden">
            Logs
          </h1>
          <label>
            <span className="label">Service</span>
            <select value={service} onChange={(e) => setService(e.target.value)}>
              <option value="">All services</option>
              {services.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
          </label>
          <label>
            <span className="label">Level</span>
            <select value={level} onChange={(e) => setLevel(e.target.value)}>
              {LEVELS.map((l) => (
                <option key={l} value={l}>
                  {l || "Any"}
                </option>
              ))}
            </select>
          </label>
          <label className="search grow">
            <span className="label">Text</span>
            <input
              ref={searchRef}
              type="search"
              placeholder="Search messages  ( / )"
              value={text}
              onChange={(e) => setText(e.target.value)}
            />
          </label>
          <button
            type="button"
            className={`button ${paused ? "primary" : ""}`}
            aria-pressed={paused}
            onClick={() => (paused ? resume() : onPausedChange(true))}
          >
            {paused ? "▶ Resume" : "❚❚ Pause"} <kbd>space</kbd>
          </button>
        </div>
        <p className="muted tail-status" role="status">
          {paused
            ? newWhilePaused
              ? `Paused · ${newWhilePaused} new lines waiting`
              : "Paused"
            : `Live · newest first · refreshes every ${POLL_MS / 1000}s`}
          {error && <span className="error"> · {error}</span>}
        </p>
        {rows.length === 0 ? (
          <p className="muted pad">No log lines match.</p>
        ) : (
          <div className="table-wrap logtable">
            <table>
              <caption className="visually-hidden">Log lines, newest first</caption>
              <thead>
                <tr>
                  <th scope="col">Time (UTC)</th>
                  <th scope="col">Service</th>
                  <th scope="col">Level</th>
                  <th scope="col">Message</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((e) => (
                  <tr
                    key={e.id}
                    className={`lvl-${e.level.toLowerCase()} ${e.id === contextId ? "selected" : ""}`}
                  >
                    <td className="nowrap">
                      <time dateTime={e.timestamp} title={formatDateTime(e.timestamp)}>
                        {formatTime(e.timestamp)}
                      </time>
                    </td>
                    <td className="nowrap">{e.service}</td>
                    <td>
                      <span className="lvl">{e.level}</span>
                    </td>
                    <td className="msg">
                      <button type="button" className="rowbutton" onClick={() => onContext(e.id)}>
                        <span className="visually-hidden">Show context for: </span>
                        {e.message}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
      {contextId !== null && <Context id={contextId} onClose={() => onContext(null)} />}
    </div>
  );
}

function Context({ id, onClose }: { id: number; onClose: () => void }) {
  const [lines, setLines] = useState<LogEvent[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const target = useRef<HTMLLIElement>(null);
  const list = useRef<HTMLOListElement>(null);
  useEffect(() => {
    setLines(null);
    api.context(id).then(setLines, (e: unknown) => setError(e instanceof Error ? e.message : String(e)));
  }, [id]);
  // Centre the chosen line inside the panel only; scrollIntoView would scroll the page too
  useEffect(() => {
    const box = list.current;
    const row = target.current;
    if (box && row) box.scrollTop = row.offsetTop - box.offsetTop - box.clientHeight / 2;
  }, [lines]);
  return (
    <aside className="context" aria-labelledby="context-title">
      <header className="context-head">
        <h2 id="context-title">Context{lines?.[0] && ` · ${lines[0].service}`}</h2>
        <button type="button" className="icon-button" onClick={onClose} aria-label="Close context">
          ×
        </button>
      </header>
      {error && <p className="error pad">{error}</p>}
      {!lines && !error && <p className="muted pad">Loading…</p>}
      {lines && (
        <ol ref={list} className="loglines context-lines">
          {lines.map((e) => (
            <li
              key={e.id}
              ref={e.id === id ? target : undefined}
              className={`lvl-${e.level.toLowerCase()} ${e.id === id ? "target" : ""}`}
              aria-current={e.id === id ? "true" : undefined}
            >
              <time dateTime={e.timestamp}>{formatTime(e.timestamp)}</time>
              <span className="lvl">{e.level}</span>
              <span className="msg">{e.message}</span>
            </li>
          ))}
        </ol>
      )}
    </aside>
  );
}
