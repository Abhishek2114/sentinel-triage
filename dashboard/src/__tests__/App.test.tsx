import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Incident, Overview, PublicConfig } from "../api";
import { App, parseRoute, routeHash } from "../App";
import { detail, incident } from "./fixtures";

/** Stands in for the browser WebSocket so tests can push events. */
class FakeSocket {
  static instances: FakeSocket[] = [];
  sent: string[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((m: { data: string }) => void) | null = null;
  onclose: (() => void) | null = null;
  constructor(public url: string) {
    FakeSocket.instances.push(this);
  }
  close() {
    this.onclose?.();
  }
  send(data: string) {
    this.sent.push(data);
  }
  open() {
    act(() => this.onopen?.());
  }
  push(event: { type: string; incident: Incident }) {
    act(() => this.onmessage?.({ data: JSON.stringify(event) }));
  }
}

let server: Incident[];
let config: PublicConfig;
let calls: { url: string; init?: RequestInit }[];

function overview(): Overview {
  const open = server.filter((i) => i.status === "open");
  return {
    verdict: open.length
      ? { level: "incident", headline: `${open.length} open incident`, detail: "payment-service (likely CPU spike)." }
      : { level: "ok", headline: "All clear", detail: "5 services reporting." },
    as_of: "2026-10-27T12:00:00Z",
    anchored: false,
    window_minutes: 30,
    services: [
      {
        service: "payment-service",
        status: open.length ? "incident" : "healthy",
        events: 600,
        errors: 42,
        error_rate: 0.07,
        last_seen: "2026-10-27T11:59:00Z",
        open_incident_id: open[0]?.id ?? null,
        errors_per_minute: Array(30).fill(1),
        events_per_minute: Array(30).fill(20),
      },
    ],
    services_truncated: false,
    open_incidents: open,
  };
}

function mockApi() {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({ url, init });
      const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });
      if (url === "/api/v1/config") return json(config);
      if (url.startsWith("/api/v1/overview")) return json(overview());
      if (url.startsWith("/api/v1/incidents?")) return json(server);
      if (url.startsWith("/api/v1/events")) return json([]);
      if (url === "/api/v1/demo/run")
        return json({ status: "started", lines: 1200, services: [], fault: "cpu_spike", detail: "Synthetic cpu spike injected." }, 202);
      const match = url.match(/^\/api\/v1\/incidents\/(\d+)$/);
      if (match) {
        const found = server.find((i) => i.id === Number(match[1]))!;
        if (init?.method === "PATCH") {
          const body = JSON.parse(String(init.body)) as { status: Incident["status"]; resolution?: string };
          Object.assign(found, { ...body, updated_at: "2026-10-27T13:00:00Z" });
          return json(found);
        }
        return json({ ...detail(found), similar_incidents: [] });
      }
      return new Response("not found", { status: 404 });
    }),
  );
}

function renderApp() {
  return render(<App openSocket={(url) => new FakeSocket(url) as unknown as WebSocket} />);
}

beforeEach(() => {
  FakeSocket.instances = [];
  calls = [];
  server = [incident()];
  config = {
    version: "0.2.0",
    demo_mode: false,
    read_requires_key: false,
    write_requires_key: false,
    llm_provider: null,
    llm_calls_today: 0,
    llm_calls_limit: 200,
  };
  sessionStorage.clear();
  window.location.hash = "";
  mockApi();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("routing", () => {
  it("round-trips hashes", () => {
    expect(parseRoute("#/incidents/12")).toMatchObject({ view: "incidents", incident: 12 });
    expect(parseRoute("#/logs?service=api&event=9")).toMatchObject({ view: "logs", service: "api", event: 9 });
    expect(parseRoute("#/nonsense").view).toBe("overview");
    expect(routeHash({ view: "logs", service: "a b" })).toBe("#/logs?service=a+b");
  });
});

describe("App", () => {
  it("answers 'is anything wrong?' first, with the service board", async () => {
    renderApp();
    expect(await screen.findByRole("heading", { level: 1, name: "1 open incident" })).toBeTruthy();
    const board = screen.getByRole("list", { name: "Services" });
    expect(within(board).getByText("payment-service")).toBeTruthy();
    expect(within(board).getByText("Incident")).toBeTruthy();
    expect(screen.getByRole("img", { name: /payment-service: 600 events and 30 errors/ })).toBeTruthy();
  });

  it("navigates with number keys and opens help with ?", async () => {
    renderApp();
    await screen.findByRole("heading", { level: 1, name: "1 open incident" });
    fireEvent.keyDown(window, { key: "2" });
    await waitFor(() => expect(window.location.hash).toBe("#/incidents"));
    fireEvent.keyDown(window, { key: "?" });
    expect(await screen.findByRole("heading", { name: "Keyboard shortcuts" })).toBeTruthy();
  });

  it("adds live incidents and announces them to screen readers", async () => {
    window.location.hash = "#/incidents";
    renderApp();
    await screen.findByText("#1");
    FakeSocket.instances[0].open();
    FakeSocket.instances[0].push({
      type: "incident.created",
      incident: incident({ id: 2, service: "user-service", runbook: "db_connection_failure.md" }),
    });
    expect(await screen.findByText("user-service")).toBeTruthy();
    expect(screen.getByText("New incident #2 on user-service: DB connection failure")).toBeTruthy();
    // And a toast in the corner that opens it
    const toast = screen.getByText("New incident #2").closest(".toast") as HTMLElement;
    expect(within(toast).getByText("user-service · DB connection failure")).toBeTruthy();
    fireEvent.click(within(toast).getByText("Open incident"));
    await waitFor(() => expect(window.location.hash).toBe("#/incidents/2"));
  });

  it("renders the triage and resolves with a note for incident memory", async () => {
    window.location.hash = "#/incidents/1";
    renderApp();
    expect(await screen.findByRole("heading", { name: /payment-service: CPU spike/ })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "What happened" })).toBeTruthy();
    expect(screen.getByText("CPU usage at <NUM>%")).toBeTruthy();
    fireEvent.change(screen.getByLabelText(/What fixed it/), { target: { value: "Scaled out to 6 pods" } });
    fireEvent.click(screen.getByRole("button", { name: "Resolve incident" }));
    await waitFor(() => expect(server[0].status).toBe("resolved"));
    expect(server[0].resolution).toBe("Scaled out to 6 pods");
  });

  it("sends the API key only in headers, never in a URL", async () => {
    config.write_requires_key = true;
    config.read_requires_key = true;
    sessionStorage.setItem("sentinel.apiKey", "secret-key-123");
    renderApp();
    await screen.findByRole("heading", { level: 1, name: "1 open incident" });
    FakeSocket.instances[0].open();
    expect(calls.every((c) => !c.url.includes("secret-key-123"))).toBe(true);
    expect(FakeSocket.instances[0].url).not.toContain("secret-key-123");
    const overviewCall = calls.find((c) => c.url.startsWith("/api/v1/overview"))!;
    expect((overviewCall.init?.headers as Record<string, string>).Authorization).toBe("Bearer secret-key-123");
    expect(JSON.parse(FakeSocket.instances[0].sent[0])).toEqual({ type: "auth", key: "secret-key-123" });
  });

  it("labels the public demo as synthetic and runs it", async () => {
    config.demo_mode = true;
    config.write_requires_key = true;
    renderApp();
    expect((await screen.findByText(/shows/)).textContent).toContain("synthetic");
    fireEvent.click(await screen.findByRole("button", { name: "Run demo" }));
    expect(await screen.findByText("Synthetic cpu spike injected.")).toBeTruthy();
    const progress = screen.getByRole("list", { name: "Demo progress" });
    expect(within(progress).getByText(/Detect \+ triage/).textContent).toContain("active");
    expect(within(progress).getByText("1,200 lines · 0 services")).toBeTruthy();

    // The pipeline completes when the demo's incident arrives over the WebSocket
    FakeSocket.instances[0].open();
    FakeSocket.instances[0].push({ type: "incident.created", incident: incident({ id: 7, service: "demo-order-service" }) });
    expect(await within(progress).findByText("#7 opened")).toBeTruthy();
    expect(within(progress).getByText(/^Incident/).textContent).toContain("done");
    expect(screen.getByText("Incident #7 raised.")).toBeTruthy();
  });

  it("hides write actions without a key when writes need one", async () => {
    config.write_requires_key = true;
    window.location.hash = "#/incidents/1";
    renderApp();
    await screen.findByRole("heading", { name: /payment-service: CPU spike/ });
    expect(screen.queryByRole("button", { name: "Resolve incident" })).toBeNull();
    expect(screen.getByRole("button", { name: "Add an API key" })).toBeTruthy();
  });
});
