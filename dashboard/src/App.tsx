import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, api, apiKey, type Overview as OverviewData, type PublicConfig } from "./api";
import { Dialog } from "./components/Dialog";
import { Incidents } from "./components/Incidents";
import { Logs } from "./components/Logs";
import { Onboarding } from "./components/Onboarding";
import { Overview } from "./components/Overview";
import { runbookTitle } from "./format";
import { useLiveIncidents } from "./useLiveIncidents";

type View = "overview" | "incidents" | "logs" | "ship";
interface Route {
  view: View;
  incident: number | null;
  service: string;
  event: number | null;
}

const VIEWS: { view: View; label: string; key: string }[] = [
  { view: "overview", label: "Overview", key: "1" },
  { view: "incidents", label: "Incidents", key: "2" },
  { view: "logs", label: "Logs", key: "3" },
  { view: "ship", label: "Ship logs", key: "4" },
];

// #/incidents/12, #/logs?service=api&event=99, #/ship
export function parseRoute(hash: string): Route {
  const [path, search = ""] = hash.replace(/^#\/?/, "").split("?");
  const [view, id] = path.split("/");
  const params = new URLSearchParams(search);
  const known = VIEWS.some((v) => v.view === view);
  return {
    view: known ? (view as View) : "overview",
    incident: view === "incidents" && id ? Number(id) || null : null,
    service: params.get("service") ?? "",
    event: Number(params.get("event")) || null,
  };
}

export function routeHash(r: Partial<Route> & { view: View }): string {
  if (r.view === "incidents") return r.incident ? `#/incidents/${r.incident}` : "#/incidents";
  if (r.view === "logs") {
    const params = new URLSearchParams();
    if (r.service) params.set("service", r.service);
    if (r.event) params.set("event", String(r.event));
    const qs = params.toString();
    return qs ? `#/logs?${qs}` : "#/logs";
  }
  return r.view === "ship" ? "#/ship" : "#/";
}

type Theme = "light" | "dark" | "system";

function useTheme(): [Theme, () => void] {
  const [theme, setTheme] = useState<Theme>(() => {
    try {
      return (localStorage.getItem("sentinel.theme") as Theme | null) ?? "system";
    } catch {
      return "system";
    }
  });
  useEffect(() => {
    const root = document.documentElement;
    if (theme === "system") root.removeAttribute("data-theme");
    else root.setAttribute("data-theme", theme);
    try {
      localStorage.setItem("sentinel.theme", theme);
    } catch {
      // per-viewer convenience only
    }
  }, [theme]);
  const cycle = () => setTheme((t) => (t === "system" ? "dark" : t === "dark" ? "light" : "system"));
  return [theme, cycle];
}

function isTyping(target: EventTarget | null): boolean {
  const el = target as HTMLElement | null;
  return !!el && (el.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName));
}

export function App({ openSocket }: { openSocket?: (url: string) => WebSocket } = {}) {
  const [route, setRoute] = useState<Route>(() => parseRoute(window.location.hash));
  const [config, setConfig] = useState<PublicConfig | null>(null);
  const [overview, setOverview] = useState<OverviewData | null>(null);
  const [overviewError, setOverviewError] = useState<string | null>(null);
  const [hasKey, setHasKey] = useState(() => Boolean(apiKey.get()));
  const [keyOpen, setKeyOpen] = useState(false);
  const [helpOpen, setHelpOpen] = useState(false);
  const [paused, setPaused] = useState(false);
  const [announcement, setAnnouncement] = useState("");
  const [theme, cycleTheme] = useTheme();
  const searchRef = useRef<HTMLInputElement>(null);
  const live = useLiveIncidents(openSocket);

  const go = useCallback((r: Partial<Route> & { view: View }) => {
    window.location.hash = routeHash(r);
  }, []);
  useEffect(() => {
    const onHash = () => setRoute(parseRoute(window.location.hash));
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  const refreshOverview = useCallback(async () => {
    try {
      setOverview(await api.overview());
      setOverviewError(null);
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) setKeyOpen(true);
      setOverviewError(e instanceof Error ? e.message : String(e));
    }
  }, []);
  useEffect(() => {
    api.config().then(setConfig, () => setConfig(null));
    void refreshOverview();
    const timer = setInterval(refreshOverview, 15_000);
    return () => clearInterval(timer);
  }, [refreshOverview, hasKey]);

  // New or changed incidents: refresh the board and tell screen-reader users
  useEffect(() => {
    const event = live.lastEvent;
    if (!event) return;
    void refreshOverview();
    const i = event.incident;
    const cause = runbookTitle(i.runbook);
    setAnnouncement(
      event.type === "incident.created"
        ? `New incident #${i.id} on ${i.service ?? "all services"}${cause ? `: ${cause}` : ""}`
        : `Incident #${i.id} ${i.status === "resolved" ? "resolved" : "updated"}`,
    );
  }, [live.lastEvent, refreshOverview]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.metaKey || e.ctrlKey || e.altKey || isTyping(e.target)) return;
      const view = VIEWS.find((v) => v.key === e.key);
      if (view) {
        e.preventDefault();
        go({ view: view.view });
      } else if (e.key === "/") {
        e.preventDefault();
        searchRef.current?.focus();
      } else if (e.key === "?") {
        e.preventDefault();
        setHelpOpen(true);
      } else if (e.key === " " && route.view === "logs" && !(e.target instanceof HTMLButtonElement)) {
        e.preventDefault();
        setPaused((p) => !p);
      } else if (e.key === "Escape" && route.event) {
        go({ view: "logs", service: route.service });
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [go, route]);

  const canWrite = !config?.write_requires_key || hasKey;
  const openCount = live.incidents.filter((i) => i.status === "open").length;
  const services = overview?.services.map((s) => s.service).sort() ?? [];
  const level = overview?.verdict.level;

  return (
    <div className="app">
      <a className="skip" href="#main">
        Skip to content
      </a>
      <header className="topbar">
        <div className="brand">
          <span className={`pulse pulse-${level ?? "no_data"}`} aria-hidden="true" />
          <span className="brand-name">Sentinel Triage</span>
        </div>
        <nav aria-label="Main">
          <ul>
            {VIEWS.map((v) => (
              <li key={v.view}>
                <a
                  href={routeHash({ view: v.view })}
                  aria-current={route.view === v.view ? "page" : undefined}
                  aria-keyshortcuts={v.key}
                >
                  {v.label}
                  {v.view === "incidents" && openCount > 0 && (
                    <span className="count alert" aria-label={`${openCount} open`}>
                      {openCount}
                    </span>
                  )}
                </a>
              </li>
            ))}
          </ul>
        </nav>
        <div className="tools">
          <span className={`conn conn-${live.connection}`} title="Live updates">
            <span aria-hidden="true">●</span> {live.connection === "live" ? "Live" : "Reconnecting"}
          </span>
          <button type="button" className="icon-button" onClick={cycleTheme} aria-label={`Theme: ${theme}. Change theme`}>
            {theme === "dark" ? "☾" : theme === "light" ? "☀" : "◐"}
          </button>
          <button type="button" className="button ghost small" onClick={() => setKeyOpen(true)}>
            {hasKey ? "Key set" : "API key"}
          </button>
          <button type="button" className="icon-button" onClick={() => setHelpOpen(true)} aria-label="Keyboard shortcuts">
            ?
          </button>
        </div>
      </header>

      {config?.demo_mode && (
        <p className="banner" role="note">
          Public demo: read-only, and every <code>demo-</code> service shows <strong>synthetic</strong> data.
        </p>
      )}

      <main id="main" tabIndex={-1}>
        {route.view === "overview" && (
          <Overview
            data={overview}
            error={overviewError}
            canRunDemo={Boolean(config?.demo_mode) || hasKey || config?.write_requires_key === false}
            demoMode={Boolean(config?.demo_mode)}
            onOpenIncident={(id) => go({ view: "incidents", incident: id })}
            onShowLogs={(service) => go({ view: "logs", service })}
            onDemoStarted={() => setTimeout(refreshOverview, 4000)}
          />
        )}
        {route.view === "incidents" && (
          <Incidents
            incidents={live.incidents}
            loaded={live.loaded}
            selectedId={route.incident}
            canWrite={canWrite}
            onSelect={(id) => go({ view: "incidents", incident: id })}
            onChanged={(incident) => {
              live.replace(incident);
              void refreshOverview();
            }}
            onNeedKey={() => setKeyOpen(true)}
            onShowContext={(event) => go({ view: "logs", event })}
            searchRef={searchRef}
          />
        )}
        {route.view === "logs" && (
          <Logs
            services={services}
            initialService={route.service}
            contextId={route.event}
            onContext={(event) => go({ view: "logs", service: route.service, event })}
            paused={paused}
            onPausedChange={setPaused}
            searchRef={searchRef}
          />
        )}
        {route.view === "ship" && (
          <Onboarding origin={window.location.origin} writeRequiresKey={config?.write_requires_key ?? true} />
        )}
      </main>

      <div className="visually-hidden" role="status" aria-live="polite">
        {announcement}
      </div>

      <Dialog open={keyOpen} title="API key" onClose={() => setKeyOpen(false)}>
        <KeyForm
          hasKey={hasKey}
          onSave={(key) => {
            apiKey.set(key);
            setHasKey(Boolean(key));
            setKeyOpen(false);
          }}
        />
      </Dialog>
      <Dialog open={helpOpen} title="Keyboard shortcuts" onClose={() => setHelpOpen(false)}>
        <dl className="shortcuts">
          {[
            ["1 – 4", "Overview, Incidents, Logs, Ship logs"],
            ["/", "Focus the search box"],
            ["j / k  or  ↓ / ↑", "Next / previous incident"],
            ["Enter", "Open the selected incident"],
            ["Space", "Pause / resume the live log tail"],
            ["Esc", "Close a dialog or the context panel"],
            ["?", "This help"],
          ].map(([k, d]) => (
            <div key={k}>
              <dt>
                <kbd>{k}</kbd>
              </dt>
              <dd>{d}</dd>
            </div>
          ))}
        </dl>
      </Dialog>
    </div>
  );
}

function KeyForm({ hasKey, onSave }: { hasKey: boolean; onSave: (key: string | null) => void }) {
  const [value, setValue] = useState("");
  return (
    <form
      className="keyform"
      onSubmit={(e) => {
        e.preventDefault();
        onSave(value.trim() || null);
      }}
    >
      <p className="muted">
        Kept in this tab's session storage and sent only in the <code>Authorization</code> header. Closing the tab
        forgets it.
      </p>
      <label htmlFor="api-key">Key</label>
      <input
        id="api-key"
        type="password"
        autoComplete="off"
        value={value}
        onChange={(e) => setValue(e.target.value)}
        placeholder={hasKey ? "•••••••• (set)" : "Paste an API key"}
      />
      <div className="row-gap">
        <button type="submit" className="button primary">
          Save
        </button>
        {hasKey && (
          <button type="button" className="button ghost" onClick={() => onSave(null)}>
            Forget key
          </button>
        )}
      </div>
    </form>
  );
}
