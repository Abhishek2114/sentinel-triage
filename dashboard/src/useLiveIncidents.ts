import { useCallback, useEffect, useRef, useState } from "react";

import { alertsUrl, api, apiKey, type Incident, type IncidentEvent } from "./api";
import { applyEvent, mergeIncidents, parseEvent, upsertIncident } from "./incidents";

export type Connection = "connecting" | "live" | "reconnecting";

const MIN_BACKOFF_MS = 1_000;
const MAX_BACKOFF_MS = 15_000;

/**
 * Incidents from the API, kept current by the WebSocket alert stream.
 *
 * On every (re)connect the list is refetched, so events missed while the
 * socket was down are never lost.
 */
export function useLiveIncidents(
  openSocket: (url: string) => WebSocket = (url) => new WebSocket(url),
) {
  const [incidents, setIncidents] = useState<Incident[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [connection, setConnection] = useState<Connection>("connecting");
  const [lastEvent, setLastEvent] = useState<IncidentEvent | null>(null);
  const openSocketRef = useRef(openSocket);

  const refresh = useCallback(async () => {
    try {
      const fetched = await api.incidents();
      setIncidents((current) => mergeIncidents(current, fetched));
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoaded(true);
    }
  }, []);

  const replace = useCallback((incident: Incident) => {
    setIncidents((current) => upsertIncident(current, incident));
  }, []);

  useEffect(() => {
    let socket: WebSocket | null = null;
    let retry: ReturnType<typeof setTimeout> | undefined;
    let backoff = MIN_BACKOFF_MS;
    let stopped = false;

    const connect = () => {
      socket = openSocketRef.current(alertsUrl());
      socket.onopen = () => {
        // Private deployments expect the key as the first message (never in the URL)
        const key = apiKey.get();
        if (key) socket?.send(JSON.stringify({ type: "auth", key }));
        backoff = MIN_BACKOFF_MS;
        setConnection("live");
        void refresh();
      };
      socket.onmessage = (message: MessageEvent<string>) => {
        const event = parseEvent(message.data);
        if (!event) return;
        setIncidents((current) => applyEvent(current, event));
        setLastEvent(event);
      };
      socket.onclose = () => {
        if (stopped) return;
        setConnection("reconnecting");
        retry = setTimeout(connect, backoff);
        backoff = Math.min(backoff * 2, MAX_BACKOFF_MS);
      };
    };

    void refresh();
    connect();
    return () => {
      stopped = true;
      clearTimeout(retry);
      socket?.close();
    };
  }, [refresh]);

  return { incidents, loaded, error, connection, lastEvent, refresh, replace };
}
