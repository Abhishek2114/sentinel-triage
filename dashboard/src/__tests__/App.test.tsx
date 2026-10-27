import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Incident } from "../api";
import { App } from "../App";
import { detail, incident } from "./fixtures";

/** Stands in for the browser WebSocket so tests can push events. */
class FakeSocket {
  static instances: FakeSocket[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((m: { data: string }) => void) | null = null;
  onclose: (() => void) | null = null;
  constructor(public url: string) {
    FakeSocket.instances.push(this);
  }
  close() {
    this.onclose?.();
  }
  open() {
    act(() => this.onopen?.());
  }
  send(event: { type: string; incident: Incident }) {
    act(() => this.onmessage?.({ data: JSON.stringify(event) }));
  }
  drop() {
    act(() => this.onclose?.());
  }
}

let server: Incident[];

function mockApi() {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const json = (body: unknown) => new Response(JSON.stringify(body), { status: 200 });
      if (url.startsWith("/api/v1/incidents?")) return json(server);
      if (url.startsWith("/api/v1/anomalies")) return json([]);
      const match = url.match(/^\/api\/v1\/incidents\/(\d+)$/);
      if (match) {
        const found = server.find((i) => i.id === Number(match[1]))!;
        if (init?.method === "PATCH") {
          const { status } = JSON.parse(String(init.body)) as Pick<Incident, "status">;
          Object.assign(found, { status, updated_at: "2026-10-27T13:00:00Z" });
          return json(found);
        }
        return json(detail(found));
      }
      return new Response("not found", { status: 404 });
    }),
  );
}

const renderApp = () =>
  render(<App openSocket={(url) => new FakeSocket(url) as unknown as WebSocket} />);

const cards = () =>
  within(screen.getByRole("region", { name: "Incidents" })).queryAllByRole("listitem");

describe("App", () => {
  beforeEach(() => {
    FakeSocket.instances = [];
    server = [incident({ id: 1 })];
    mockApi();
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  it("shows incidents and the selected incident's detail", async () => {
    renderApp();
    expect(await screen.findByRole("heading", { name: /payment-service · CPU Spike/ })).toBeTruthy();
    expect(screen.getByText("CPU usage at <NUM>%")).toBeTruthy();
    expect(screen.getByText("42.9×")).toBeTruthy();
    expect(screen.getByText("Immediate: Scale horizontally")).toBeTruthy();
  });

  it("goes live, adds incidents from events, and keeps the selection", async () => {
    renderApp();
    await screen.findByRole("heading", { name: /payment-service/ });
    const socket = FakeSocket.instances[0];
    expect(socket.url).toMatch(/\/api\/v1\/ws\/alerts$/);
    socket.open();
    expect(screen.getByRole("status").textContent).toContain("Live");

    socket.send({
      type: "incident.created",
      incident: incident({
        id: 2,
        service: "api-gateway",
        runbook: "db_connection_failure.md",
        last_seen_at: "2024-06-15T05:00:00Z",
      }),
    });

    await waitFor(() => expect(cards()).toHaveLength(2));
    expect(screen.getByText("DB Connection Failure")).toBeTruthy();
    expect(screen.getByText("New incident on api-gateway")).toBeTruthy();
    // Still reading incident 1
    expect(screen.getByRole("heading", { name: /payment-service/ })).toBeTruthy();
  });

  it("updates a grouped incident in place", async () => {
    renderApp();
    await screen.findByRole("heading", { name: /payment-service/ });
    FakeSocket.instances[0].open();

    FakeSocket.instances[0].send({
      type: "incident.updated",
      incident: incident({ anomaly_count: 3, updated_at: "2026-10-27T11:00:00Z" }),
    });

    await waitFor(() => expect(screen.getAllByText(/3 anomalies/).length).toBeGreaterThan(0));
    expect(cards()).toHaveLength(1);
  });

  it("resolves an incident and moves it between filters", async () => {
    renderApp();
    await screen.findByRole("heading", { name: /payment-service/ });

    fireEvent.click(screen.getByRole("button", { name: "Resolve" }));

    await waitFor(() => expect(screen.getByRole("button", { name: "Reopen" })).toBeTruthy());
    expect(screen.getByRole("tab", { name: /Open/ }).textContent).toContain("0");
    expect(screen.getByRole("tab", { name: /Resolved/ }).textContent).toContain("1");
  });

  it("reconnects after the socket drops and refetches", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    renderApp();
    await screen.findByRole("heading", { name: /payment-service/ });
    FakeSocket.instances[0].open();

    FakeSocket.instances[0].drop();
    expect(screen.getByRole("status").textContent).toContain("Reconnecting");

    server.push(incident({ id: 5, service: "user-service", last_seen_at: "2024-06-15T04:00:00Z" }));
    await act(async () => {
      vi.advanceTimersByTime(1_100);
    });
    expect(FakeSocket.instances).toHaveLength(2);
    FakeSocket.instances[1].open();

    // Missed while disconnected, picked up by the refetch on reconnect
    await waitFor(() => expect(cards()).toHaveLength(2));
    expect(screen.getByRole("status").textContent).toContain("Live");
  });

  it("shows an empty state with next steps when there are no incidents", async () => {
    server = [];
    renderApp();
    expect(await screen.findByRole("heading", { name: "No incidents yet" })).toBeTruthy();
  });
});
