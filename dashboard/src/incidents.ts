import type { Incident, IncidentEvent, IncidentStatus } from "./api";

/** Most recently active first, like GET /incidents. */
export function sortIncidents(incidents: Incident[]): Incident[] {
  return [...incidents].sort(
    (a, b) => Date.parse(b.last_seen_at) - Date.parse(a.last_seen_at) || b.id - a.id,
  );
}

/** Insert or replace one incident. Ignores an event older than what we have. */
export function upsertIncident(incidents: Incident[], incoming: Incident): Incident[] {
  const current = incidents.find((i) => i.id === incoming.id);
  if (current && Date.parse(current.updated_at) > Date.parse(incoming.updated_at)) {
    return incidents;
  }
  return sortIncidents([...incidents.filter((i) => i.id !== incoming.id), incoming]);
}

/**
 * Fold a fetched list into the current one. Never replaces wholesale: a live
 * event can arrive while the fetch is in flight, and the older snapshot must
 * not erase the incident (or the update) that event just delivered.
 */
export function mergeIncidents(current: Incident[], fetched: Incident[]): Incident[] {
  return fetched.reduce(upsertIncident, current);
}

export function applyEvent(incidents: Incident[], event: IncidentEvent): Incident[] {
  return upsertIncident(incidents, event.incident);
}

export type StatusFilter = IncidentStatus | "all";

export function filterIncidents(incidents: Incident[], status: StatusFilter): Incident[] {
  return status === "all" ? incidents : incidents.filter((i) => i.status === status);
}

export function parseEvent(raw: string): IncidentEvent | null {
  try {
    const data = JSON.parse(raw) as Partial<IncidentEvent>;
    if (
      (data.type === "incident.created" || data.type === "incident.updated") &&
      typeof data.incident?.id === "number"
    ) {
      return data as IncidentEvent;
    }
  } catch {
    // fall through: not ours
  }
  return null;
}
