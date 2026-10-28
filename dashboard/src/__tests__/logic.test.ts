import { describe, expect, it } from "vitest";

import { formatAgo, formatDuration, formatTime, percent, plural, runbookTitle } from "../format";
import {
  filterIncidents,
  mergeIncidents,
  parseEvent,
  sortIncidents,
  upsertIncident,
} from "../incidents";
import { incident } from "./fixtures";

describe("incident list state", () => {
  it("sorts by most recent activity, newest id first on ties", () => {
    const a = incident({ id: 1, last_seen_at: "2024-06-15T01:00:00Z" });
    const b = incident({ id: 2, last_seen_at: "2024-06-15T03:00:00Z" });
    const c = incident({ id: 3, last_seen_at: "2024-06-15T01:00:00Z" });
    expect(sortIncidents([a, b, c]).map((i) => i.id)).toEqual([2, 3, 1]);
  });

  it("upserts new and updated incidents", () => {
    const list = [incident({ id: 1 })];
    const added = upsertIncident(list, incident({ id: 2, last_seen_at: "2024-06-15T05:00:00Z" }));
    expect(added.map((i) => i.id)).toEqual([2, 1]);

    const grouped = upsertIncident(
      added,
      incident({ id: 1, anomaly_count: 2, updated_at: "2026-10-27T11:00:00Z" }),
    );
    expect(grouped.find((i) => i.id === 1)?.anomaly_count).toBe(2);
    expect(grouped).toHaveLength(2);
  });

  it("ignores an event older than the copy it already has", () => {
    const fresh = [incident({ status: "resolved", updated_at: "2026-10-27T12:00:00Z" })];
    const stale = incident({ status: "open", updated_at: "2026-10-27T11:00:00Z" });
    expect(upsertIncident(fresh, stale)).toBe(fresh);
  });

  it("a refetch never drops an incident that arrived live meanwhile", () => {
    const live = [incident({ id: 2, last_seen_at: "2024-06-15T05:00:00Z" }), incident({ id: 1 })];
    const olderSnapshot = [incident({ id: 1 })];
    expect(mergeIncidents(live, olderSnapshot).map((i) => i.id)).toEqual([2, 1]);
  });

  it("filters by status", () => {
    const list = [incident({ id: 1 }), incident({ id: 2, status: "resolved" })];
    expect(filterIncidents(list, "open").map((i) => i.id)).toEqual([1]);
    expect(filterIncidents(list, "resolved").map((i) => i.id)).toEqual([2]);
    expect(filterIncidents(list, "all")).toHaveLength(2);
  });

  it("accepts only well-formed incident events", () => {
    const event = { type: "incident.created", incident: incident() };
    expect(parseEvent(JSON.stringify(event))).toEqual(event);
    expect(parseEvent("not json")).toBeNull();
    expect(parseEvent(JSON.stringify({ type: "something.else", incident: incident() }))).toBeNull();
    expect(parseEvent(JSON.stringify({ type: "incident.created" }))).toBeNull();
  });
});

describe("formatting", () => {
  it("formats times in UTC", () => {
    expect(formatTime("2024-06-15T01:02:03Z")).toBe("01:02:03");
  });

  it("formats durations", () => {
    expect(formatDuration("2024-06-15T01:00:00Z", "2024-06-15T01:14:00Z")).toBe("14 min");
    expect(formatDuration("2024-06-15T01:00:00Z", "2024-06-15T02:05:00Z")).toBe("1 h 5 min");
  });

  it("pluralises", () => {
    expect(plural(1, "anomaly", "anomalies")).toBe("1 anomaly");
    expect(plural(2, "anomaly", "anomalies")).toBe("2 anomalies");
    expect(plural(3, "incident")).toBe("3 incidents");
  });

  it("titles runbooks with acronyms", () => {
    expect(runbookTitle("db_connection_failure.md")).toBe("DB connection failure");
    expect(runbookTitle("cpu_spike.md")).toBe("CPU spike");
    expect(runbookTitle(null)).toBeNull();
  });

  it("formats relative times and rates for the board", () => {
    const now = Date.parse("2026-10-27T12:00:00Z");
    expect(formatAgo("2026-10-27T11:59:40Z", now)).toBe("just now");
    expect(formatAgo("2026-10-27T11:55:00Z", now)).toBe("5 min ago");
    expect(formatAgo("2026-10-27T09:00:00Z", now)).toBe("3 h ago");
    expect(percent(0)).toBe("0%");
    expect(percent(0.004)).toBe("<1%");
    expect(percent(0.256)).toBe("26%");
  });
});
