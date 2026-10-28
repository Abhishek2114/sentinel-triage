import { useState } from "react";

interface Props {
  origin: string;
  writeRequiresKey: boolean;
}

function Command({ title, code, note }: { title: string; code: string; note?: string }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(code);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      setCopied(false);
    }
  };
  return (
    <div className="command">
      <div className="command-head">
        <h3>{title}</h3>
        <button type="button" className="button ghost" onClick={copy}>
          {copied ? "Copied" : "Copy"}
          <span className="visually-hidden"> command: {title}</span>
        </button>
      </div>
      <pre tabIndex={0}>
        <code>{code}</code>
      </pre>
      {note && <p className="fine">{note}</p>}
      <span className="visually-hidden" role="status">
        {copied ? "Copied to clipboard" : ""}
      </span>
    </div>
  );
}

export function Onboarding({ origin, writeRequiresKey }: Props) {
  const auth = writeRequiresKey ? ' \\\n  -H "Authorization: Bearer $SENTINEL_API_KEY"' : "";
  const keyLine = writeRequiresKey ? "export SENTINEL_API_KEY=<your key>   # never put it in the URL\n" : "";
  const now = new Date().toISOString().replace(/\.\d+Z$/, "Z");
  return (
    <section className="onboarding" aria-labelledby="ship-title">
      <h1 id="ship-title">Ship logs</h1>
      <p className="lead">
        Send lines to <code>{origin}/api/v1/ingest</code>. Timestamps are read from each line; secrets and emails are
        redacted before anything is stored; stack traces are joined into one event.
      </p>
      <ol className="steps-list">
        <li>
          <Command
            title="1. Check the connection with one line"
            code={`${keyLine}curl -X POST ${origin}/api/v1/ingest${auth} \\
  -H "Content-Type: application/json" \\
  -d '{"lines": ["${now} checkout ERROR payment provider timeout after 3000ms"]}'`}
            note='Expect {"ingested": 1, "errors": 0}. The line appears under Logs right away.'
          />
        </li>
        <li>
          <Command
            title="2. Follow log files with the agent (Python 3.9+, no dependencies)"
            code={`curl -fsSLo sentinel_ship.py ${origin}/agent/sentinel_ship.py
${keyLine}python3 sentinel_ship.py --url ${origin} --follow '/var/log/myapp/*.log'`}
            note="Survives log rotation, retries with backoff when the server is unreachable, and never splits a stack trace across requests."
          />
        </li>
        <li>
          <Command
            title="3. Or pipe any command's output"
            code={`kubectl logs -f deploy/checkout | python3 sentinel_ship.py --url ${origin} --service checkout -
docker logs -f checkout 2>&1 | python3 sentinel_ship.py --url ${origin} --service checkout -`}
            note="--service names lines that don't carry their own service, so they aren't filed under “unknown”."
          />
        </li>
      </ol>
      <h2>Accepted line formats</h2>
      <ul className="formats">
        <li>
          <code>2026-10-27T14:03:00Z checkout ERROR message</code> (ISO-8601, service, level)
        </li>
        <li>
          <code>Oct 27 14:03:00 host checkout[123]: message</code> (syslog)
        </li>
        <li>
          <code>{'{"timestamp": "...", "service": "checkout", "level": "ERROR", "message": "..."}'}</code> (JSON)
        </li>
      </ul>
      <p className="fine">
        Limits: 5 MB per request, 16 KB per line (longer lines are cut and marked), timestamps more than 5 minutes in
        the future are replaced by the time received.
      </p>
    </section>
  );
}
