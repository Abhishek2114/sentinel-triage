#!/usr/bin/env python3
"""Evaluation script – compute precision, recall, and MTTD (Phase 3 stub).

Usage:
    python scripts/run_eval.py --labels data/synthetic/labels.jsonl
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate anomaly detection performance")
    parser.add_argument(
        "--labels",
        type=str,
        default="data/synthetic/labels.jsonl",
        help="Path to ground-truth labels",
    )
    parser.add_argument(
        "--output",
        type=str,
        default="eval/results/baseline.json",
        help="Output path for results",
    )
    args = parser.parse_args()

    labels_path = Path(args.labels)
    if not labels_path.exists():
        print(f"Labels file not found: {labels_path}")
        print("Run `python scripts/generate_logs.py` first.")
        return

    labels = []
    with open(labels_path) as f:
        for line in f:
            labels.append(json.loads(line))

    print(f"Loaded {len(labels)} fault labels.")
    print("TODO: Phase 3 – query anomalies from DB and compute metrics.")

    # Placeholder output
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    results = {
        "detector": "iforest",
        "precision": None,
        "recall": None,
        "mttd_seconds": None,
        "num_labels": len(labels),
        "status": "not_yet_computed",
    }
    out_path.write_text(json.dumps(results, indent=2))
    print(f"Placeholder results saved to {out_path}")


if __name__ == "__main__":
    main()
