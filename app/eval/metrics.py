"""Match detected anomalies to ground-truth faults and compute metrics.

Matching rules (kept deliberately simple so the numbers are easy to audit):

- An anomaly is a **true positive** if it overlaps a fault window *on the same
  service*. An alert on a neighbouring service counts as a false positive,
  because it would send the on-call engineer to the wrong place.
- A fault is **detected** if at least one anomaly is a true positive for it.
- **Time to detect** for a fault is when its first matching alert could have
  fired – the end of the first flagged window – minus the fault's start,
  floored at zero. It measures the detector, not the scheduler: in production
  the Celery beat interval adds up to one more interval of delay.
"""

from __future__ import annotations

import statistics
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from app.detect.pipeline import FoundAnomaly


@dataclass(frozen=True, slots=True)
class FaultLabel:
    """One injected fault, as written to ``labels.jsonl`` by the generator."""

    fault_type: str
    service: str
    start: datetime
    end: datetime

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> FaultLabel:
        return cls(
            fault_type=str(raw["fault_type"]),
            service=str(raw["service"]),
            start=datetime.fromisoformat(raw["window_start"]),
            end=datetime.fromisoformat(raw["window_end"]),
        )


def overlaps(anomaly: FoundAnomaly, fault: FaultLabel) -> bool:
    """Same service and the half-open intervals intersect."""
    return (
        anomaly.service == fault.service
        and anomaly.window_start < fault.end
        and anomaly.window_end > fault.start
    )


@dataclass
class DetectorReport:
    """Counts for one detector, summable across runs."""

    detector: str
    anomalies: int = 0
    true_positives: int = 0
    faults: int = 0
    detected: int = 0
    hours: float = 0.0
    delays_seconds: list[float] = field(default_factory=list)
    # fault_type -> [detected, total]
    by_fault_type: dict[str, list[int]] = field(default_factory=dict)

    @property
    def false_positives(self) -> int:
        return self.anomalies - self.true_positives

    @property
    def precision(self) -> float:
        # No alerts at all is reported as 0 rather than undefined: a silent
        # detector shouldn't look perfect
        return self.true_positives / self.anomalies if self.anomalies else 0.0

    @property
    def recall(self) -> float:
        return self.detected / self.faults if self.faults else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if p + r else 0.0

    @property
    def mttd_seconds(self) -> float | None:
        return statistics.fmean(self.delays_seconds) if self.delays_seconds else None

    @property
    def median_ttd_seconds(self) -> float | None:
        return statistics.median(self.delays_seconds) if self.delays_seconds else None

    @property
    def false_alarms_per_day(self) -> float:
        return self.false_positives / self.hours * 24 if self.hours else 0.0

    def add(self, other: DetectorReport) -> None:
        """Fold another run's counts into this one (micro-averaging)."""
        self.anomalies += other.anomalies
        self.true_positives += other.true_positives
        self.faults += other.faults
        self.detected += other.detected
        self.hours += other.hours
        self.delays_seconds.extend(other.delays_seconds)
        for fault_type, (hit, total) in other.by_fault_type.items():
            counts = self.by_fault_type.setdefault(fault_type, [0, 0])
            counts[0] += hit
            counts[1] += total

    def to_dict(self) -> dict[str, Any]:
        return {
            "detector": self.detector,
            "precision": round(self.precision, 4),
            "recall": round(self.recall, 4),
            "f1": round(self.f1, 4),
            "mttd_seconds": _round(self.mttd_seconds),
            "median_ttd_seconds": _round(self.median_ttd_seconds),
            "false_alarms_per_day": round(self.false_alarms_per_day, 2),
            "anomalies": self.anomalies,
            "true_positives": self.true_positives,
            "faults": self.faults,
            "detected": self.detected,
            "hours": round(self.hours, 2),
            "recall_by_fault_type": {
                ft: {"detected": hit, "total": total}
                for ft, (hit, total) in sorted(self.by_fault_type.items())
            },
        }


def _round(value: float | None) -> float | None:
    return None if value is None else round(value, 1)


def score_detector(
    detector: str,
    anomalies: Iterable[FoundAnomaly],
    faults: Sequence[FaultLabel],
    hours: float,
    window_minutes: int = 1,
) -> DetectorReport:
    """Score one detector's anomalies from one run against that run's faults."""
    ours = [a for a in anomalies if a.detector == detector]
    window = timedelta(minutes=window_minutes)
    report = DetectorReport(detector=detector, anomalies=len(ours), faults=len(faults), hours=hours)

    report.true_positives = sum(1 for a in ours if any(overlaps(a, f) for f in faults))

    for fault in faults:
        counts = report.by_fault_type.setdefault(fault.fault_type, [0, 0])
        counts[1] += 1
        alert_times = [a.window_start + window for a in ours if overlaps(a, fault)]
        if not alert_times:
            continue
        counts[0] += 1
        report.detected += 1
        report.delays_seconds.append(max(0.0, (min(alert_times) - fault.start).total_seconds()))

    return report


@dataclass
class RetrievalReport:
    """How often triage picked the right runbook for a caught fault."""

    detector: str
    embedder: str
    # fault_type -> [right runbook on top, faults evaluated]
    by_fault_type: dict[str, list[int]] = field(default_factory=dict)
    # (fault_type, runbook picked instead) for each miss
    misses: list[tuple[str, str | None]] = field(default_factory=list)

    def record(self, fault_type: str, top_runbook: str | None) -> None:
        counts = self.by_fault_type.setdefault(fault_type, [0, 0])
        counts[1] += 1
        if top_runbook == f"{fault_type}.md":
            counts[0] += 1
        else:
            self.misses.append((fault_type, top_runbook))

    @property
    def evaluated(self) -> int:
        return sum(total for _, total in self.by_fault_type.values())

    @property
    def hit_rate(self) -> float:
        hits = sum(hit for hit, _ in self.by_fault_type.values())
        return hits / self.evaluated if self.evaluated else 0.0

    def add(self, other: RetrievalReport) -> None:
        for fault_type, (hit, total) in other.by_fault_type.items():
            counts = self.by_fault_type.setdefault(fault_type, [0, 0])
            counts[0] += hit
            counts[1] += total
        self.misses.extend(other.misses)

    def to_dict(self) -> dict[str, Any]:
        return {
            "detector": self.detector,
            "embedder": self.embedder,
            "runbook_hit_rate": round(self.hit_rate, 4),
            "evaluated": self.evaluated,
            "by_fault_type": {
                ft: {"hit": hit, "total": total}
                for ft, (hit, total) in sorted(self.by_fault_type.items())
            },
            "misses": [
                {"fault_type": ft, "picked": doc}
                for ft, doc in sorted(self.misses, key=lambda m: (m[0], m[1] or ""))
            ],
        }


@dataclass
class IncidentReport:
    """What the on-call engineer would see: incidents, not anomalies."""

    grouping: str  # e.g. "none" or "15 min"
    incidents: int = 0
    faults_caught: int = 0
    # Extra incidents for a fault that already had one (the flapping problem)
    duplicates: int = 0
    # Incidents that overlap more than one fault (grouping went too far)
    merged: int = 0
    # Incidents that overlap no fault
    false_incidents: int = 0

    def add(self, other: IncidentReport) -> None:
        self.incidents += other.incidents
        self.faults_caught += other.faults_caught
        self.duplicates += other.duplicates
        self.merged += other.merged
        self.false_incidents += other.false_incidents

    @property
    def per_caught_fault(self) -> float:
        caught_incidents = self.incidents - self.false_incidents
        return caught_incidents / self.faults_caught if self.faults_caught else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "grouping": self.grouping,
            "incidents": self.incidents,
            "faults_caught": self.faults_caught,
            "incidents_per_caught_fault": round(self.per_caught_fault, 3),
            "duplicates": self.duplicates,
            "merged": self.merged,
            "false_incidents": self.false_incidents,
        }


def score_incidents(
    grouping: str, groups: Sequence[Sequence[FoundAnomaly]], faults: Sequence[FaultLabel]
) -> IncidentReport:
    """Score incidents (each a group of anomalies) against the faults."""
    report = IncidentReport(grouping=grouping, incidents=len(groups))
    hits_per_fault = [0] * len(faults)
    for group in groups:
        touched = [i for i, f in enumerate(faults) if any(overlaps(a, f) for a in group)]
        if not touched:
            report.false_incidents += 1
        if len(touched) > 1:
            report.merged += 1
        for i in touched:
            hits_per_fault[i] += 1
    report.faults_caught = sum(1 for n in hits_per_fault if n)
    report.duplicates = sum(n - 1 for n in hits_per_fault if n > 1)
    return report
