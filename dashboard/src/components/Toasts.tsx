import { useEffect, useState } from "react";

import type { IncidentEvent } from "../api";
import { runbookTitle } from "../format";
import { motionAllowed } from "../motion";

const SHOW_MS = 7000;
const MAX_TOASTS = 3;

interface Toast {
  key: number;
  event: IncidentEvent;
  leaving: boolean;
}

interface Props {
  event: IncidentEvent | null;
  onOpen: (id: number) => void;
}

/**
 * A card per live incident event, sliding in from the corner. The countdown
 * bar dismisses it and pauses while hovered. Screen readers already get the
 * same news from the app's live region, so this stack is visual only.
 */
export function Toasts({ event, onOpen }: Props) {
  const [toasts, setToasts] = useState<Toast[]>([]);

  useEffect(() => {
    if (!event) return;
    setToasts((t) => [...t, { key: Date.now() + Math.random(), event, leaving: false }].slice(-MAX_TOASTS));
  }, [event]);

  const leave = (key: number) => {
    if (!motionAllowed()) {
      setToasts((t) => t.filter((x) => x.key !== key));
      return;
    }
    setToasts((t) => t.map((x) => (x.key === key ? { ...x, leaving: true } : x)));
  };
  const remove = (key: number) => setToasts((t) => t.filter((x) => x.key !== key));

  // Without animations nothing ends the countdown, so a timer does
  useEffect(() => {
    if (motionAllowed() || toasts.length === 0) return;
    const timer = setTimeout(() => setToasts((t) => t.slice(1)), SHOW_MS);
    return () => clearTimeout(timer);
  }, [toasts]);

  return (
    <div className="toasts" aria-hidden="true">
      {toasts.map(({ key, event: e, leaving }) => {
        const i = e.incident;
        const kind = e.type === "incident.created" ? "created" : i.status === "resolved" ? "resolved" : "updated";
        const title =
          kind === "created"
            ? `New incident #${i.id}`
            : kind === "resolved"
              ? `Incident #${i.id} resolved`
              : `Incident #${i.id} updated`;
        const cause = runbookTitle(i.runbook) ?? "cause unknown";
        const body =
          kind === "updated" && i.anomaly_count > 1
            ? `${i.service ?? "all services"} · ${i.anomaly_count} anomalies grouped`
            : `${i.service ?? "all services"} · ${cause}`;
        return (
          <div
            key={key}
            className={`toast toast-${kind}${leaving ? " leaving" : ""}`}
            style={{ "--toast-ms": `${SHOW_MS}ms` } as React.CSSProperties}
            onAnimationEnd={(ev) => {
              if (ev.animationName === "countdown") leave(key);
              else if (ev.animationName === "toast-out") remove(key);
            }}
          >
            <span className="toast-icon">{kind === "created" ? "!" : kind === "resolved" ? "✓" : "↻"}</span>
            <div>
              <p className="toast-title">{title}</p>
              <p className="toast-body">{body}</p>
              <button
                type="button"
                className="link"
                tabIndex={-1}
                onClick={() => {
                  onOpen(i.id);
                  leave(key);
                }}
              >
                Open incident
              </button>
            </div>
            <button type="button" className="icon-button" tabIndex={-1} onClick={() => leave(key)}>
              ×
            </button>
            <span className="toast-timer" />
          </div>
        );
      })}
    </div>
  );
}
