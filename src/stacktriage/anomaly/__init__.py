"""Unusual-activity detection over time windows.

Three separate quantities, never conflated:

* `WindowFinding.score`          statistical anomaly score, 0..1, 0.5 = fitted threshold
* `WindowFinding.classification` rule-based kind of unusual pattern (classify.py)
* `WindowFinding.severity`       operational severity from episode evidence (severity.py)

Findings are evidence of unusual windows, with the contributing features listed. They never
state a root cause.
"""
from .detect import AUTO_METHOD, METHODS, detect_anomalies
from .severity import EpisodeEvidence, Grade, grade
from .types import CLASSIFICATIONS, SEVERITIES, AnomalyReport, Episode, WindowFinding

__all__ = [
    "AUTO_METHOD", "AnomalyReport", "CLASSIFICATIONS", "Episode", "EpisodeEvidence", "Grade",
    "METHODS", "SEVERITIES", "WindowFinding", "detect_anomalies", "grade",
]
