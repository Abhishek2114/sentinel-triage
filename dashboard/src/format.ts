// Log timestamps are UTC, and on-call people compare them with log lines,
// so everything is shown in UTC rather than the browser's zone.

const DATE_TIME = new Intl.DateTimeFormat("en-GB", {
  month: "short",
  day: "numeric",
  hour: "2-digit",
  minute: "2-digit",
  timeZone: "UTC",
});

const TIME = new Intl.DateTimeFormat("en-GB", {
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  timeZone: "UTC",
});

export function formatDateTime(iso: string): string {
  return `${DATE_TIME.format(new Date(iso))} UTC`;
}

export function formatTime(iso: string): string {
  return TIME.format(new Date(iso));
}

export function formatDuration(startIso: string, endIso: string): string {
  const minutes = Math.max(0, Math.round((Date.parse(endIso) - Date.parse(startIso)) / 60_000));
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  return `${hours} h ${minutes % 60} min`;
}

export function plural(n: number, one: string, many = `${one}s`): string {
  return `${n} ${n === 1 ? one : many}`;
}

export function formatAgo(iso: string, now: number = Date.now()): string {
  const seconds = Math.max(0, Math.round((now - Date.parse(iso)) / 1000));
  if (seconds < 60) return "just now";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 48) return `${hours} h ago`;
  return `${Math.round(hours / 24)} days ago`;
}

export function percent(rate: number): string {
  if (rate === 0) return "0%";
  return rate < 0.01 ? "<1%" : `${Math.round(rate * 100)}%`;
}

/** "db_connection_failure.md" → "DB connection failure" */
export function runbookTitle(name: string | null): string | null {
  if (!name) return null;
  const words = name.replace(/\.md$/, "").split("_");
  const acronyms = new Set(["db", "tls", "dns", "cpu"]);
  const first = acronyms.has(words[0])
    ? words[0].toUpperCase()
    : words[0].charAt(0).toUpperCase() + words[0].slice(1);
  return [first, ...words.slice(1)].join(" ");
}
