#!/usr/bin/env python3
"""Evaluate detectors against labelled synthetic faults.

Generates seeded runs of synthetic logs (deterministic, so anyone gets the same
numbers), runs every detector through the production detection code, and
reports precision, recall, F1 and time to detect. It also checks triage: for
each fault the error-burst detector catches, does evidence + retrieval put the
right runbook first?

The default writes eval/results/heldout.json, which is committed: rerunning
unchanged code reproduces it byte for byte, so `git diff` shows exactly how a
change moved the numbers.

Usage:
    python scripts/run_eval.py                       # held-out seeds 1000-1049 (~1 min)
    python scripts/run_eval.py --seeds 1000-1004     # quick check
    python scripts/run_eval.py --logs data/synthetic/events.log \\
        --labels data/synthetic/labels.jsonl         # score one existing run
    python scripts/run_eval.py --embedder all-MiniLM-L6-v2 \\
        --output eval/results/minilm.json            # triage with the real model

Seeds 1000+ were never used while tuning detector settings; keep tuning on
other seeds so these numbers stay honest.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

# Allow `python scripts/run_eval.py` from the repo root without installing scripts/
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.detect.pipeline import DEFAULT_DETECTORS, DETECTORS  # noqa: E402
from app.eval.harness import RunbookIndex, evaluate_run  # noqa: E402
from app.eval.metrics import DetectorReport, RetrievalReport  # noqa: E402
from app.rag.embeddings import HASHING, get_embedder  # noqa: E402
from app.rag.ingest_docs import load_runbooks  # noqa: E402
from scripts.generate_logs import generate_logs  # noqa: E402

HELD_OUT_SEEDS = "1000-1049"  # 50 runs, 200 faults


def parse_seeds(spec: str) -> list[int]:
    """Parse "1000-1009" or "1,5,9" (or a mix) into a sorted list of seeds."""
    seeds: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            lo, hi = (int(x) for x in part.split("-", 1))
            if hi < lo:
                raise argparse.ArgumentTypeError(f"Bad seed range: {part}")
            seeds.update(range(lo, hi + 1))
        elif part:
            seeds.add(int(part))
    if not seeds:
        raise argparse.ArgumentTypeError("No seeds given")
    return sorted(seeds)


def _fmt_seconds(value: float | None) -> str:
    return "–" if value is None else f"{value / 60:.1f} min"


def markdown_table(reports: dict[str, DetectorReport]) -> str:
    """The README results table."""
    rows = [
        "| Detector | Precision | Recall | F1 | MTTD | False alarms / day |",
        "|---|---|---|---|---|---|",
    ]
    for r in reports.values():
        rows.append(
            f"| `{r.detector}` | {r.precision:.2f} | {r.recall:.2f} | {r.f1:.2f} "
            f"| {_fmt_seconds(r.mttd_seconds)} | {r.false_alarms_per_day:.1f} |"
        )
    fault_types = sorted({ft for r in reports.values() for ft in r.by_fault_type})
    rows += [
        "",
        "| Recall by fault type | "
        + " | ".join(f"`{r.detector}`" for r in reports.values())
        + " |",
        "|---|" + "---|" * len(reports),
    ]
    for ft in fault_types:
        cells = []
        for r in reports.values():
            hit, total = r.by_fault_type.get(ft, [0, 0])
            cells.append(f"{hit}/{total}")
        rows.append(f"| {ft} | " + " | ".join(cells) + " |")
    return "\n".join(rows)


def retrieval_table(report: RetrievalReport) -> str:
    """Triage: right runbook ranked first, per fault type."""
    rows = [
        f"| Right runbook first (`{report.detector}` catches, `{report.embedder}` embedder) | |",
        "|---|---|",
    ]
    for ft, (hit, total) in sorted(report.by_fault_type.items()):
        rows.append(f"| {ft} | {hit}/{total} |")
    rows.append(f"| **All** | **{report.hit_rate:.0%}** ({report.evaluated} faults) |")
    return "\n".join(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate anomaly detectors on labelled faults")
    parser.add_argument(
        "--seeds",
        type=parse_seeds,
        default=parse_seeds(HELD_OUT_SEEDS),
        help=f"Generator seeds, e.g. 1000-1009 or 1,2,3 (default {HELD_OUT_SEEDS})",
    )
    parser.add_argument("--hours", type=int, default=6, help="Hours of logs per run")
    parser.add_argument("--epm", type=int, default=20, help="Events per minute per run")
    parser.add_argument("--faults", type=int, default=4, help="Faults injected per run")
    parser.add_argument("--logs", type=Path, help="Score an existing log file instead")
    parser.add_argument("--labels", type=Path, help="Labels for --logs")
    parser.add_argument(
        "--detectors",
        nargs="+",
        choices=sorted(DETECTORS),
        default=list(DEFAULT_DETECTORS),
    )
    parser.add_argument("--window-minutes", type=int, default=1)
    parser.add_argument(
        "--embedder",
        default=HASHING,
        help="Embedder for the triage check (default: the offline hashing embedder, so "
        "results reproduce without downloading a model)",
    )
    parser.add_argument("--runbooks-dir", type=Path, default=Path("data/runbooks"))
    parser.add_argument("--no-triage", action="store_true", help="Skip the runbook check")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("eval/results/heldout.json"),
        help="Where to write the JSON report",
    )
    args = parser.parse_args()

    if (args.logs is None) != (args.labels is None):
        parser.error("--logs and --labels go together")

    runs: list[tuple[str, list[str], list[dict[str, Any]]]] = []
    if args.logs is not None:
        if not args.logs.exists() or not args.labels.exists():
            parser.error(
                "Log or label file not found. Run `python scripts/generate_logs.py` first."
            )
        lines = args.logs.read_text(encoding="utf-8").splitlines()
        with args.labels.open(encoding="utf-8") as f:
            labels = [json.loads(line) for line in f if line.strip()]
        runs.append((str(args.logs), lines, labels))
        source: dict[str, Any] = {"logs": str(args.logs), "labels": str(args.labels)}
    else:
        for seed in args.seeds:
            lines, labels = generate_logs(
                duration_hours=args.hours,
                events_per_minute=args.epm,
                fault_count=args.faults,
                seed=seed,
            )
            runs.append((f"seed {seed}", lines, labels))
        source = {
            "generator": {
                "seeds": args.seeds,
                "hours": args.hours,
                "events_per_minute": args.epm,
                "faults_per_run": args.faults,
            }
        }

    index = None
    if not args.no_triage:
        index = RunbookIndex(get_embedder(args.embedder), load_runbooks(args.runbooks_dir))
        if index.embedder.name != args.embedder:
            parser.error(f"Could not load embedder {args.embedder!r}")

    totals = {name: DetectorReport(detector=name) for name in args.detectors}
    triage_total: RetrievalReport | None = None
    per_run: list[dict[str, Any]] = []
    for run_name, lines, labels in runs:
        result_run = evaluate_run(
            lines, labels, args.detectors, args.window_minutes, runbooks=index
        )
        for name, report in result_run.detectors.items():
            totals[name].add(report)
        run_entry: dict[str, Any] = {
            "run": run_name,
            "detectors": [r.to_dict() for r in result_run.detectors.values()],
        }
        if result_run.retrieval is not None:
            if triage_total is None:
                triage_total = RetrievalReport(
                    result_run.retrieval.detector, result_run.retrieval.embedder
                )
            triage_total.add(result_run.retrieval)
            run_entry["triage"] = result_run.retrieval.to_dict()
        per_run.append(run_entry)
        print(
            f"  {run_name:>10}: "
            + "  ".join(
                f"{r.detector} P={r.precision:.2f} R={r.recall:.2f}"
                for r in result_run.detectors.values()
            )
        )

    faults = next(iter(totals.values())).faults
    print(f"\n{len(runs)} run(s), {faults} labelled faults\n")
    print(markdown_table(totals))
    if triage_total is not None:
        print("\n" + retrieval_table(triage_total))

    result: dict[str, Any] = {
        "source": source,
        "window_minutes": args.window_minutes,
        "detectors": [r.to_dict() for r in totals.values()],
    }
    if triage_total is not None:
        result["triage"] = triage_total.to_dict()
    result["runs"] = per_run
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(f"\nSaved {args.output}")


if __name__ == "__main__":
    main()
