interface Props {
  errors: number[];
  events: number[];
  label: string;
}

/** Events per minute as grey bars with the error share in red, one bar per minute. */
export function Sparkline({ errors, events, label }: Props) {
  const max = Math.max(1, ...events);
  const width = events.length * 4;
  const peakErrors = Math.max(0, ...errors);
  const summary =
    `${label}: ${events.reduce((a, b) => a + b, 0)} events and ` +
    `${errors.reduce((a, b) => a + b, 0)} errors in ${events.length} minutes, ` +
    `peak ${peakErrors} errors in one minute`;
  return (
    <svg
      className="spark"
      viewBox={`0 0 ${width} 24`}
      preserveAspectRatio="none"
      role="img"
      aria-label={summary}
    >
      <title>{summary}</title>
      {events.map((n, i) => {
        const h = (n / max) * 22;
        const e = (Math.min(errors[i] ?? 0, n) / max) * 22;
        return (
          <g key={i} style={{ "--i": i } as React.CSSProperties}>
            <rect x={i * 4} y={24 - h} width={3} height={h} className="spark-events" />
            {e > 0 && <rect x={i * 4} y={24 - e} width={3} height={e} className="spark-errors" />}
          </g>
        );
      })}
    </svg>
  );
}
