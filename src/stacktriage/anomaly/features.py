"""Per-window features and the robust (median / MAD) baseline they are compared against."""
from __future__ import annotations

import math
import statistics
from collections.abc import Sequence
from dataclasses import dataclass

from ..timeline import Bucket

BASE_FEATURES = ("error_count", "event_rate", "error_ratio", "new_groups")
METRIC_PREFIX = "metric."

# Which direction of deviation is unusual. +1: only increases (fewer errors is not an
# incident), -1: only decreases (a traffic surge alone is not an incident; a quiet period
# is), 0: both (numeric metrics have unknown polarity).
_DIRECTION = {"error_count": 1, "error_ratio": 1, "new_groups": 1, "event_rate": -1}

# Smallest believable noise level per feature. Median/MAD collapses to zero for sparse
# counts, so the scale never goes below these floors.
NEW_GROUPS_SCALE = 0.1  # one new error group is 10 "sigma" over an all-zero baseline
ERROR_RATIO_SCALE = 0.01
RELATIVE_SCALE = 0.1  # event_rate: at least 10 % of the median
METRIC_RELATIVE_SCALE = 0.05  # numeric metrics: at least 5 % of |median|
MAD_TO_SIGMA = 1.4826
Z_CLIP = 50.0


def direction(name: str) -> int:
    return _DIRECTION.get(name, 0)


def observed(bucket: Bucket) -> dict[str, float]:
    """Observed feature values of one window. Metrics are present only if some event carried them."""
    d = {
        "error_count": float(bucket.errors),
        "event_rate": bucket.event_rate,
        "error_ratio": bucket.error_ratio,
        "new_groups": float(bucket.new_groups),
    }
    for k, v in bucket.metrics.items():
        if math.isfinite(v):
            d[METRIC_PREFIX + k] = float(v)
    return d


@dataclass(frozen=True, slots=True)
class FeatureStat:
    name: str
    median: float
    scale: float
    direction: int

    def z(self, value: float) -> float:
        """Signed robust z-score; a missing value (NaN) counts as typical."""
        if math.isnan(value):
            return 0.0
        return max(-Z_CLIP, min(Z_CLIP, (value - self.median) / self.scale))

    def excess(self, z: float) -> float:
        """Deviation in the unusual direction only (>= 0)."""
        if self.direction > 0:
            return max(z, 0.0)
        if self.direction < 0:
            return max(-z, 0.0)
        return abs(z)


def _scale_floor(name: str, median: float) -> float:
    if name == "error_count":
        return math.sqrt(max(median, 1.0))  # Poisson noise
    if name == "new_groups":
        return NEW_GROUPS_SCALE
    if name == "error_ratio":
        return ERROR_RATIO_SCALE
    if name == "event_rate":
        return max(RELATIVE_SCALE * median, 1e-9)
    return max(METRIC_RELATIVE_SCALE * abs(median), 1e-9)


def fit_stat(name: str, values: Sequence[float]) -> FeatureStat:
    vals = [v for v in values if not math.isnan(v)]
    if not vals:
        return FeatureStat(name, 0.0, _scale_floor(name, 0.0), direction(name))
    med = statistics.median(vals)
    mad = statistics.median(abs(v - med) for v in vals)
    return FeatureStat(name, med, max(MAD_TO_SIGMA * mad, _scale_floor(name, med)), direction(name))


def quantile(sorted_vals: Sequence[float], q: float) -> float:
    """Linear-interpolated quantile of an already sorted sequence."""
    if not sorted_vals:
        return 0.0
    pos = q * (len(sorted_vals) - 1)
    lo = int(math.floor(pos))
    hi = min(lo + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (pos - lo)
