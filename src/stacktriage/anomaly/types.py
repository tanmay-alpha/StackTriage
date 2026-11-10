"""Result types of window anomaly detection. Plain data only: no models, no pickles."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

CLASSIFICATIONS = ("normal", "spike", "sustained_shift", "new_error_type", "quiet_period", "unusual_pattern")
SEVERITIES = ("low", "medium", "high", "critical")


@dataclass(slots=True)
class WindowFinding:
    """One time window.

    `score` is a statistical anomaly score only (0..1, 0.5 = the decision threshold fitted on
    the training period, comparable only within one report). `classification` says which
    kind of unusual pattern it is (rule based). `severity` is operational and comes from the
    evidence of the episode the window belongs to, never from `score` (None when the window
    is not anomalous).
    """

    start: datetime
    end: datetime
    score: float
    is_anomalous: bool
    classification: str
    severity: str | None
    features: dict[str, float]  # observed values
    # top contributing features: {"feature", "observed", "baseline", "deviation"} where
    # `deviation` is the signed robust z-score against the training-period baseline
    contributions: list[dict[str, Any]] = field(default_factory=list)
    used_for_training: bool = False  # True when this window was part of the baseline fit


@dataclass(slots=True)
class Episode:
    """Consecutive anomalous windows merged into one candidate incident."""

    start: datetime
    end: datetime
    peak_score: float
    classification: str  # classification of the peak-score window
    severity: str  # from `evidence`, see severity.py
    window_count: int
    evidence: list[str] = field(default_factory=list)  # human-readable severity inputs
    features: list[str] = field(default_factory=list)  # most deviating features (not a root cause)


@dataclass(slots=True)
class AnomalyReport:
    method_used: str  # "statistical" | "isolation_forest"
    requested_method: str
    windows: list[WindowFinding]  # one per bucket; unscored (score 0) when the report has notes only
    train_range: tuple[datetime, datetime] | None
    episodes: list[Episode] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    # fitted baseline as plain JSON-safe data (medians, scales, thresholds). Informational:
    # nothing is ever loaded back from it.
    parameters: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["train_range"] = [t.isoformat() for t in self.train_range] if self.train_range else None
        for w in d["windows"]:
            w["start"], w["end"] = w["start"].isoformat(), w["end"].isoformat()
        for e in d["episodes"]:
            e["start"], e["end"] = e["start"].isoformat(), e["end"].isoformat()
        return d
