"""Anomaly classification: *what kind* of unusual window this is, by explicit rules.

Independent of the anomaly score and of operational severity. Input is the per-feature
robust z-score of an already flagged window (against the training baseline).

Rules, first match wins (DEV_Z = 3.0 robust sigmas in the unusual direction = "deviates"):

1. `new_error_type`  new_groups deviates and nothing else does (low-volume novelty).
2. `quiet_period`    event_rate is below baseline (deviates downward) and error_count does
                     not deviate (traffic dropped without an error surge: silent failure,
                     outage of an upstream, a gap in logging).
3. `deviation`       error_count, error_ratio, a numeric metric, or new_groups together with
                     other features deviate. Resolved by duration into `spike` (run of
                     fewer than SUSTAINED_MIN_WINDOWS anomalous windows) or `sustained_shift`
                     (a run of at least that many consecutive anomalous windows).
4. `unusual_pattern` flagged by the model but no single feature reaches DEV_Z (typical for a
                     multivariate Isolation Forest finding). Contributions still list the
                     largest deviations.
"""
from __future__ import annotations

from collections.abc import Mapping

DEV_Z = 3.0
SUSTAINED_MIN_WINDOWS = 3


def window_pattern(excess: Mapping[str, float], z: Mapping[str, float]) -> str:
    """Pattern of one flagged window: new_error_type | quiet_period | deviation | unusual_pattern.

    `excess` is the deviation in the unusual direction per feature, `z` the signed value.
    """
    deviating = {f for f, e in excess.items() if e >= DEV_Z}
    if not deviating:
        return "unusual_pattern"
    if deviating == {"new_groups"}:
        return "new_error_type"
    if z.get("event_rate", 0.0) <= -DEV_Z and "error_count" not in deviating:
        return "quiet_period"
    return "deviation"


def final_classification(pattern: str, run_length: int) -> str:
    """Resolve `deviation` into spike / sustained_shift using the anomalous run length."""
    if pattern != "deviation":
        return pattern
    return "sustained_shift" if run_length >= SUSTAINED_MIN_WINDOWS else "spike"
