"""Slow-drift (trend) detection: one-sided tabular CUSUM on robust z-scores.

The point detector compares every window with the training baseline on its own, so a slow
ramp stays below the threshold until its late, steep part. A CUSUM accumulates small
deviations instead:

    S_0 = 0,   S_t = min(S_cap, max(0, S_{t-1} + x_t - k))     alarm while S_t >= h

`x_t` is the robust z-score of one feature in its unusual direction, re-centred and re-scaled
by the mean / standard deviation of that series on the training windows (median-centred
z-scores of skewed counts have a positive mean in healthy data, which a CUSUM would
accumulate). `k` is the reference value (drift per window, in training standard deviations,
that is ignored), `h` the decision threshold.

* Channels: `error_count` and `error_ratio` upward, every numeric metric up *and* down (two
  one-sided sums, metrics have unknown polarity). `event_rate` and `new_groups` are left to the
  point detector (traffic drops are step changes; novelty is a single-window signal).
* Windows the point detector already flags do not feed the sums (S is held): spikes are
  reported by the point detector, and an isolated burst must not leave a long CUSUM tail.
* S is capped at `S_CAP_FACTOR * h`, so once the series is back at its training mean an alarm
  ends after about (S_CAP_FACTOR - 1) * h / k windows instead of lingering.
* The standardisation (mean, sd) and `h` per channel come from the training windows only, in
  time order, point-flagged ones held: h = max(H_FLOOR, H_MARGIN * largest S reached there).
  No labels and no window after the training period are used.

Constants were chosen on the development seeds only (docs/ml-evaluation.md, section 5b).
"""
from __future__ import annotations

import math
import statistics
from collections.abc import Sequence
from dataclasses import dataclass

K_REF = 1.0  # training sds per window that are ignored (reference value k)
H_FLOOR = 5.0  # smallest decision threshold h (training sds)
H_MARGIN = 1.0  # h >= margin x the largest CUSUM reached on the training windows
S_CAP_FACTOR = 1.25  # S never exceeds this multiple of h
SD_FLOOR = 0.25  # smallest training sd of a channel (robust sigmas), avoids amplifying flat series


@dataclass(frozen=True, slots=True)
class Channel:
    feature: str
    sign: int  # +1: upward drift, -1: downward drift (of the signed robust z)


def channels(names: Sequence[str], directions: Sequence[int]) -> list[Channel]:
    """Trend channels for the features of one report (see module docstring)."""
    out: list[Channel] = []
    for name, d in zip(names, directions):
        if name in ("error_count", "error_ratio") and d > 0:
            out.append(Channel(name, 1))
        elif d == 0:
            out.extend((Channel(name, 1), Channel(name, -1)))
    return out


def cusum(xs: Sequence[float], k: float, *, hold: Sequence[bool] | None = None,
          cap: float = float("inf")) -> list[float]:
    """Upper one-sided tabular CUSUM. `hold[t]` keeps S unchanged at step t; S <= cap."""
    s, out = 0.0, []
    for t, x in enumerate(xs):
        if hold is None or not hold[t]:
            s = min(cap, max(0.0, s + x - k))
        out.append(s)
    return out


def first_alarm(sums: Sequence[float], h: float) -> int | None:
    return next((t for t, s in enumerate(sums) if s >= h), None)


def standardizer(train_xs: Sequence[float]) -> tuple[float, float]:
    """(mean, sd) of a channel on its training windows; sd never below SD_FLOOR."""
    if not train_xs:
        return 0.0, 1.0
    mu = statistics.fmean(train_xs)
    sd = statistics.pstdev(train_xs, mu) if len(train_xs) > 1 else 0.0
    return mu, max(sd, SD_FLOOR) if math.isfinite(sd) else 1.0


def calibrate_h(train_xs: Sequence[float], train_hold: Sequence[bool], k: float) -> float:
    """Decision threshold from the training windows only (time order, point-flagged held)."""
    peak = max(cusum(train_xs, k, hold=train_hold), default=0.0)
    return max(H_FLOOR, H_MARGIN * peak)


@dataclass(slots=True)
class TrendResult:
    raw: list[float]  # per window: max over channels of S / h (>= 1 means trend alarm)
    sums: dict[Channel, list[float]]
    thresholds: dict[Channel, float]
    centers: dict[Channel, tuple[float, float]]  # (mean, sd) of the training series


def trend_scores(
    chans: Sequence[Channel], z_cols: dict[str, Sequence[float]], hold: Sequence[bool],
    train_idx: Sequence[int],
) -> TrendResult:
    """CUSUM of every channel over all windows, thresholds fitted on `train_idx` only.

    `z_cols[feature]` is the signed robust z per window, `hold[t]` True where the point
    detector flags window t.
    """
    n = len(hold)
    raw = [0.0] * n
    sums: dict[Channel, list[float]] = {}
    thresholds: dict[Channel, float] = {}
    centers: dict[Channel, tuple[float, float]] = {}
    train_hold = [hold[i] for i in train_idx]
    for ch in chans:
        signed = [ch.sign * z for z in z_cols[ch.feature]]
        mu, sd = standardizer([signed[i] for i, held in zip(train_idx, train_hold) if not held])
        xs = [(x - mu) / sd for x in signed]
        h = calibrate_h([xs[i] for i in train_idx], train_hold, K_REF)
        s = cusum(xs, K_REF, hold=hold, cap=S_CAP_FACTOR * h)
        sums[ch], thresholds[ch], centers[ch] = s, h, (mu, sd)
        for t in range(n):
            r = s[t] / h
            if r > raw[t]:
                raw[t] = r
    return TrendResult(raw, sums, thresholds, centers)
