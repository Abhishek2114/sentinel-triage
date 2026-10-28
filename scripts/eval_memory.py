#!/usr/bin/env python3
"""Incident memory: does the most similar past incident share the new one's fault?

SYNTHETIC data. "History" is the incidents from 20 generator runs (seeds
2000-2019) treated as resolved; "queries" are the incidents from the 50
held-out runs (seeds 1000-1049). Incidents are built exactly as triage
builds them (error_burst anomalies, 15-minute grouping, lift-ranked evidence)
and memory ranks history with the production function (``rank_similar``).

An incident's fault type is the labelled fault it overlaps; a false alarm
has none. A memory hit is correct if the top past incident has the same
fault type. Baselines: the most recent past incident on the same service,
and the class prior (a random past incident).

Caveat: four synthetic fault types with fixed message templates make this
far easier than real incident history. It shows the mechanism works and
abstains when nothing matches; it does not estimate real-world accuracy.

    python scripts/eval_memory.py
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.detect.pipeline import detect_events  # noqa: E402
from app.eval.harness import _parse, _points  # noqa: E402
from app.eval.metrics import FaultLabel, overlaps  # noqa: E402
from app.triage.evidence import BASELINE_PERIOD, build_evidence, evidence_window  # noqa: E402
from app.triage.grouping import group_anomalies  # noqa: E402
from app.triage.memory import rank_similar  # noqa: E402
from scripts.generate_logs import generate_logs  # noqa: E402

HISTORY_SEEDS = range(2000, 2020)
QUERY_SEEDS = range(1000, 1050)


@dataclass
class Inc:
    run: int
    service: str
    fault: str  # fault type, or "none" for a false alarm
    patterns: str
    order: int  # position in time across the whole history


def incidents(seed: int) -> list[Inc]:
    lines, labels = generate_logs(duration_hours=6, seed=seed)
    faults = [FaultLabel.from_dict(raw) for raw in labels]
    parsed = _parse(lines)
    found = detect_events(_points(parsed), ["error_burst"])
    out = []
    for group in group_anomalies(found.anomalies, timedelta(minutes=15)):
        first = group[0]
        start, end = evidence_window(first.window_start, first.window_end)
        same = [p.event for p in parsed if p.service == first.service]
        window = [e for e in same if start <= e.timestamp < end]
        baseline = Counter(
            e.template_id for e in same if start - BASELINE_PERIOD <= e.timestamp < start
        )
        evidence = build_evidence(first.service, start, end, window, baseline)
        fault = next((f.fault_type for f in faults if any(overlaps(a, f) for a in group)), "none")
        out.append(Inc(seed, first.service, fault, evidence.query_text(), 0))
    return out


def main() -> None:
    history = [i for s in HISTORY_SEEDS for i in incidents(s)]
    for n, inc in enumerate(history):
        inc.order = n
    queries = [i for s in QUERY_SEEDS for i in incidents(s)]
    texts = [h.patterns for h in history]
    prior = Counter(h.fault for h in history)

    answered = correct = hit3 = recent_correct = 0
    abstained_false_alarms = false_alarms = 0
    labelled = [q for q in queries if q.fault != "none"]
    for q in queries:
        hits = rank_similar(q.patterns, texts, limit=3)
        if q.fault == "none":
            false_alarms += 1
            abstained_false_alarms += not hits
            continue
        if hits:
            answered += 1
            correct += history[hits[0][0]].fault == q.fault
            hit3 += any(history[i].fault == q.fault for i, _ in hits)
        same_service = [h for h in history if h.service == q.service]
        if same_service:
            recent_correct += max(same_service, key=lambda h: h.order).fault == q.fault

    n = len(labelled)
    random_expected = sum(prior[q.fault] for q in labelled) / len(history) / n
    result: dict[str, Any] = {
        "data": "synthetic",
        "history_incidents": len(history),
        "history_by_fault": dict(prior),
        "query_incidents": len(queries),
        "query_incidents_with_fault": n,
        "memory": {
            "answered": answered,
            "abstained": n - answered,
            "top1_same_fault_of_answered": round(correct / answered, 3) if answered else None,
            "top1_same_fault_of_all": round(correct / n, 3),
            "any_of_top3_same_fault_of_all": round(hit3 / n, 3),
        },
        "baselines": {
            "most_recent_same_service": round(recent_correct / n, 3),
            "random_past_incident_expected": round(random_expected, 3),
        },
        "false_alarm_queries": false_alarms,
        "false_alarms_with_no_match": abstained_false_alarms,
    }
    print(json.dumps(result, indent=2))
    out = Path("eval/results/memory.json")
    out.write_text(json.dumps(result, indent=2) + "\n")
    print(f"\nSaved {out}")


if __name__ == "__main__":
    main()
