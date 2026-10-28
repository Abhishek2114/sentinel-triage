#!/usr/bin/env python3
"""Synthetic log generator with labeled fault injection.

Generates realistic log data for multiple microservices, with configurable
fault windows that simulate real production incidents.

Fault types:
  - cpu_spike: Elevated latency and timeout errors
  - db_connection_failure: Database connection refused/timeout errors
  - bad_deploy: New error templates, 500s, panic logs
  - memory_leak: Gradually increasing memory warnings, then OOM

Output:
  - data/synthetic/events.log       – the log file
  - data/synthetic/labels.jsonl     – ground-truth fault windows
"""

from __future__ import annotations

import argparse
import json
import random
from datetime import UTC, datetime, timedelta
from pathlib import Path

# ── Constants ─────────────────────────────────────────

SERVICES = ["api-gateway", "user-service", "order-service", "payment-service", "inventory-service"]
NORMAL_LEVELS = ["INFO", "INFO", "INFO", "INFO", "DEBUG", "WARNING"]  # weighted toward INFO

NORMAL_TEMPLATES = [
    "Request completed successfully in {latency}ms",
    "Processed {count} items from queue",
    "Health check passed: all systems nominal",
    "Cache hit for key {key}",
    "Cache miss for key {key}, fetching from DB",
    "User {user_id} authenticated successfully",
    "Connection pool: {active}/{max} active connections",
    "Scheduled task {task} completed in {latency}ms",
    "Received webhook from {service}",
    "Rate limit check passed for client {client_id}",
    "Configuration reloaded successfully",
    "Metrics flush: {count} data points sent",
]

FAULT_CONFIGS = {
    "cpu_spike": {
        "error_rate": 0.4,
        "templates": [
            "Request timeout after {latency}ms (CPU overloaded)",
            "Worker thread pool exhausted, {count} requests queued",
            "Response time exceeded SLA: {latency}ms > 500ms",
            "Circuit breaker OPEN for {service}: too many timeouts",
            "CPU usage at {pct}% – throttling new requests",
        ],
    },
    "db_connection_failure": {
        "error_rate": 0.6,
        "templates": [
            "FATAL: connection to database at {ip}:{port} refused",
            "Connection pool exhausted: 0/{max} available",
            "Query timeout after {latency}ms on table {table}",
            "Retrying database connection (attempt {count}/{max})",
            "SQLSTATE 08006: connection_failure",
            "Failed to acquire connection from pool within 30s",
        ],
    },
    "bad_deploy": {
        "error_rate": 0.5,
        "templates": [
            "Unhandled exception in {handler}: NullPointerException",
            "HTTP 500 Internal Server Error on {path}",
            "PANIC: runtime error: invalid memory address",
            "Module {module} failed to initialize: missing config key {key}",
            "Version mismatch: expected v{expected} got v{actual}",
            "Rollback triggered: health checks failing",
        ],
    },
    "memory_leak": {
        "error_rate": 0.3,
        "templates": [
            "Memory usage: {pct}% of {max}MB (warning threshold: 80%)",
            "GC pause {latency}ms – heap pressure increasing",
            "Allocator warning: fragmentation at {pct}%",
            "OOM kill imminent: RSS {current}MB / {max}MB",
            "Container memory limit approaching: {pct}% used",
            "Emergency GC triggered – freed {count}MB",
        ],
    },
}

# ── Helpers ────────────────────────────────────────────


def _rand_var(template: str) -> str:
    """Fill in template variables with random realistic values."""
    replacements = {
        "{latency}": str(random.choice([120, 250, 500, 1200, 3500, 8000, 15000])),
        "{count}": str(random.randint(1, 500)),
        "{key}": f"user:{random.randint(1000, 9999)}:session",
        "{user_id}": f"usr_{random.randint(10000, 99999)}",
        "{active}": str(random.randint(1, 50)),
        "{max}": str(random.choice([50, 100, 200])),
        "{task}": random.choice(["cleanup", "sync", "reindex", "flush", "audit"]),
        "{service}": random.choice(SERVICES),
        "{client_id}": f"client_{random.randint(100, 999)}",
        "{ip}": f"10.0.{random.randint(0, 255)}.{random.randint(1, 254)}",
        "{port}": str(random.choice([5432, 3306, 6379, 27017])),
        "{table}": random.choice(["users", "orders", "payments", "inventory", "sessions"]),
        "{handler}": random.choice(
            ["UserController.get", "OrderService.create", "PaymentGateway.charge"]
        ),
        "{path}": random.choice(["/api/users", "/api/orders", "/api/payments", "/api/health"]),
        "{module}": random.choice(["auth", "billing", "notifications", "analytics"]),
        "{expected}": f"{random.randint(1, 5)}.{random.randint(0, 9)}.{random.randint(0, 20)}",
        "{actual}": f"{random.randint(1, 5)}.{random.randint(0, 9)}.{random.randint(0, 20)}",
        "{pct}": str(random.randint(75, 99)),
        "{current}": str(random.randint(800, 3800)),
    }
    result = template
    for k, v in replacements.items():
        result = result.replace(k, v)
    return result


def _format_line(ts: datetime, service: str, level: str, message: str) -> str:
    """Format a single log line in ISO-8601 format."""
    ts_str = ts.strftime("%Y-%m-%dT%H:%M:%S.") + f"{ts.microsecond // 1000:03d}Z"
    return f"{ts_str} {service} {level} {message}"


# ── Generator ─────────────────────────────────────────


def generate_logs(
    duration_hours: int = 6,
    events_per_minute: int = 20,
    fault_count: int = 4,
    seed: int = 42,
    start_time: datetime | None = None,
    service_prefix: str = "",
) -> tuple[list[str], list[dict]]:
    """Generate synthetic log lines and fault labels.

    ``start_time`` defaults to a fixed date so evaluation runs reproduce byte
    for byte; the live demo passes "an hour ago" and a ``demo-`` prefix so its
    data is recent and visibly synthetic.

    Returns:
        (log_lines, labels) where labels are dicts with fault metadata.
    """
    random.seed(seed)
    lines: list[str] = []
    labels: list[dict] = []

    start_time = start_time or datetime(2024, 6, 15, 0, 0, 0, tzinfo=UTC)
    services = [service_prefix + s for s in SERVICES]
    total_minutes = duration_hours * 60

    # Plan fault windows (non-overlapping, spread across the timeline)
    fault_types = list(FAULT_CONFIGS.keys())
    faults_to_inject = random.sample(
        fault_types * ((fault_count // len(fault_types)) + 1),
        k=fault_count,
    )

    # Space them out evenly
    gap = total_minutes // (fault_count + 1)
    fault_windows: list[dict] = []
    for i, ft in enumerate(faults_to_inject):
        fault_start = (i + 1) * gap
        # Cap duration at the gap so windows never overlap on short runs
        fault_duration = max(1, min(random.randint(5, 15), gap - 1))
        fault_service = random.choice(services)
        fault_windows.append({
            "type": ft,
            "start_minute": fault_start,
            "end_minute": fault_start + fault_duration,
            "service": fault_service,
        })
        labels.append({
            "fault_type": ft,
            "service": fault_service,
            "window_start": (start_time + timedelta(minutes=fault_start)).isoformat(),
            "window_end": (
                start_time + timedelta(minutes=fault_start + fault_duration)
            ).isoformat(),
            "duration_minutes": fault_duration,
        })

    # Generate events minute by minute
    for minute in range(total_minutes):
        current_time = start_time + timedelta(minutes=minute)
        n_events = events_per_minute + random.randint(-5, 5)

        # Check if we're in a fault window
        active_fault = None
        for fw in fault_windows:
            if fw["start_minute"] <= minute < fw["end_minute"]:
                active_fault = fw
                break

        for _ in range(max(1, n_events)):
            ts = current_time + timedelta(seconds=random.uniform(0, 59.999))
            service = random.choice(services)

            if active_fault and service == active_fault["service"]:
                # Fault mode for the affected service
                cfg = FAULT_CONFIGS[active_fault["type"]]
                if random.random() < cfg["error_rate"]:
                    level = random.choice(["ERROR", "ERROR", "CRITICAL", "WARNING"])
                    template = random.choice(cfg["templates"])
                else:
                    level = random.choice(NORMAL_LEVELS)
                    template = random.choice(NORMAL_TEMPLATES)
            else:
                # Normal operation
                level = random.choice(NORMAL_LEVELS)
                template = random.choice(NORMAL_TEMPLATES)
                # Tiny baseline error rate
                if random.random() < 0.02:
                    level = "ERROR"
                    template = random.choice([
                        "Transient error: connection reset by peer",
                        "Request failed with status {count}",
                        "Unexpected response from {service}",
                    ])

            message = _rand_var(template)
            lines.append(_format_line(ts, service, level, message))

    # Sort by timestamp
    lines.sort()
    return lines, labels


# ── CLI ───────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate synthetic logs with fault injection")
    parser.add_argument("--hours", type=int, default=6, help="Duration in hours")
    parser.add_argument("--epm", type=int, default=20, help="Events per minute (approx)")
    parser.add_argument("--faults", type=int, default=4, help="Number of fault windows")
    parser.add_argument("--seed", type=int, default=42, help="Random seed")
    parser.add_argument(
        "--output-dir",
        type=str,
        default="data/synthetic",
        help="Output directory",
    )
    args = parser.parse_args()

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"Generating {args.hours}h of logs (~{args.hours * 60 * args.epm} events)...")
    lines, labels = generate_logs(
        duration_hours=args.hours,
        events_per_minute=args.epm,
        fault_count=args.faults,
        seed=args.seed,
    )

    log_path = out_dir / "events.log"
    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"  ✓ Wrote {len(lines)} log lines to {log_path}")

    labels_path = out_dir / "labels.jsonl"
    with open(labels_path, "w", encoding="utf-8") as f:
        for label in labels:
            f.write(json.dumps(label) + "\n")
    print(f"  ✓ Wrote {len(labels)} fault labels to {labels_path}")

    print("\nInjected faults:")
    for lb in labels:
        print(f"  • {lb['fault_type']:25s} on {lb['service']:20s} "
              f"({lb['window_start']} → +{lb['duration_minutes']}m)")


if __name__ == "__main__":
    main()
