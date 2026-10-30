import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, api, apiKey, type Overview as OverviewData, type PublicConfig } from "./api";
import { Dialog } from "./components/Dialog";
import { Incidents } from "./components/Incidents";
import { Logs } from "./components/Logs";
import { Onboarding } from "./components/Onboarding";
import { Overview } from "./components/Overview";
import { Toasts } from "./components/Toasts";
import { runbookTitle } from "./format";
import { useTilt } from "./motion";
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
  useTilt();

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

  const signOut = () => {
    apiKey.set(null);
    setHasKey(false);
    setAnnouncement("Signed out");
  };

  const canWrite = !config?.write_requires_key || hasKey;
  const openCount = live.incidents.filter((i) => i.status === "open").length;
  const services = overview?.services.map((s) => s.service).sort() ?? [];
  const level = overview?.verdict.level;

  return (
    <div className="app" data-level={level ?? "no_data"}>
      <div className="scene" aria-hidden="true">
        <div className="scene-horizon" />
        <div className="scene-floor" />
        <span className="scene-orb" style={{ "--d": 14, left: "12%", top: "22%" } as React.CSSProperties} />
        <span className="scene-orb small" style={{ "--d": 30, left: "78%", top: "14%" } as React.CSSProperties} />
        <span className="scene-orb tiny" style={{ "--d": 48, left: "64%", top: "34%" } as React.CSSProperties} />
      </div>
      <a className="skip" href="#main">
        Skip to content
      </a>
      <header className="topbar">
        <div className="brand">
          <span className="cube" aria-hidden="true">
            <i />
            <i />
            <i />
            <i />
            <i />
            <i />
          </span>
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
                    <span key={openCount} className="count alert" aria-label={`${openCount} open`}>
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
          {hasKey ? (
            <>
              <span className="admin-chip" title="Signed in as admin">
                <span aria-hidden="true">●</span> Admin
              </span>
              <button type="button" className="button ghost small" onClick={signOut}>
                Sign out
              </button>
            </>
          ) : (
            config?.write_requires_key !== false && (
              <button type="button" className="button ghost small" onClick={() => setKeyOpen(true)}>
                Admin sign-in
              </button>
            )
          )}
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
        <div key={route.view} className="view">
        {route.view === "overview" && (
          <Overview
            data={overview}
            error={overviewError}
            canRunDemo={Boolean(config?.demo_mode) || hasKey || config?.write_requires_key === false}
            demoMode={Boolean(config?.demo_mode)}
            onOpenIncident={(id) => go({ view: "incidents", incident: id })}
            onShowLogs={(service) => go({ view: "logs", service })}
            onDemoStarted={() => setTimeout(refreshOverview, 4000)}
            lastEvent={live.lastEvent}
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
        </div>
      </main>

      <Toasts event={live.lastEvent} onOpen={(id) => go({ view: "incidents", incident: id })} />

      <div className="visually-hidden" role="status" aria-live="polite">
        {announcement}
      </div>

      <Dialog open={keyOpen} title="Admin sign-in" onClose={() => setKeyOpen(false)}>
        <SignInForm
          refused={hasKey}
          onSignedIn={(key) => {
            apiKey.set(key);
            setHasKey(true);
            setKeyOpen(false);
            setAnnouncement("Signed in as admin");
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

/**
 * The owner's sign-in. There are no accounts: visitors browse and run the demo
 * without one, and the owner signs in with a key from the server's API_KEYS.
 * The key is checked before it is kept, and lives only in this tab.
 */
function SignInForm({ refused, onSignedIn }: { refused: boolean; onSignedIn: (key: string) => void }) {
  const [value, setValue] = useState("");
  const [shown, setShown] = useState(false);
  const [checking, setChecking] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async () => {
    const key = value.trim();
    if (!key) {
      setError("Enter your admin key.");
      return;
    }
    setChecking(true);
    setError(null);
    try {
      await api.authCheck(key);
      onSignedIn(key);
    } catch (e) {
      setError(
        e instanceof ApiError && e.status === 401
          ? "That key isn't right. Check it was copied in full, without brackets or quotes."
          : "Couldn't reach the server. Try again in a moment.",
      );
      setChecking(false);
    }
  };

  return (
    <form
      className="signin"
      onSubmit={(e) => {
        e.preventDefault();
        void submit();
      }}
    >
      <p className="lead">
        Only the site owner signs in, to resolve incidents and send real logs. Visitors can browse everything and run
        the demo without an account.
      </p>
      {refused && (
        <p className="error" role="alert">
          The saved key was refused. Sign in again.
        </p>
      )}
      {/* Lets a password manager file the key under this site */}
      <input type="text" name="username" value="admin" autoComplete="username" readOnly hidden />
      <label htmlFor="admin-key">Admin key</label>
      <div className="secret">
        <input
          id="admin-key"
          name="password"
          type={shown ? "text" : "password"}
          autoComplete="current-password"
          spellCheck={false}
          autoFocus
          value={value}
          onChange={(e) => setValue(e.target.value)}
          aria-invalid={error ? true : undefined}
          aria-describedby={error ? "signin-error" : "signin-hint"}
        />
        <button type="button" className="button ghost small" aria-pressed={shown} onClick={() => setShown((s) => !s)}>
          {shown ? "Hide" : "Show"}
        </button>
      </div>
      {error ? (
        <p id="signin-error" className="error" role="alert">
          {error}
        </p>
      ) : (
        <p id="signin-hint" className="fine">
          The key set as <code>API_KEYS</code> on the server. It's kept in this browser tab only and forgotten when the
          tab closes.
        </p>
      )}
      <button type="submit" className="button primary" disabled={checking}>
        {checking ? "Checking…" : "Sign in"}
      </button>
    </form>
  );
}
