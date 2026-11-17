"""Window anomaly detection: baseline from an earlier healthy period, scoring of every window.

Pipeline (details and measured results: docs/ml-evaluation.md):

1. Features per window (features.py): error_count, event_rate, error_ratio, new_groups and the
   per-window mean of every numeric metric that most training windows carry.
2. Training windows: the first `train_fraction` of the timeline *by time*, minus windows that
   are extreme under a robust rule (see `_select_training`), so an incident that starts early
   does not poison the baseline. Nothing after that period is used for fitting anything.
3. Baseline (median, MAD-based scale per feature) and the decision threshold are fitted on the
   training windows only. Isolation Forest is refit from the same windows on every call.
4. Score every window (0..1, 0.5 = threshold), flag, classify (classify.py), merge consecutive
   flagged windows into episodes and grade episode severity from evidence (severity.py).

`statistical_trend` = the statistical point detector OR a one-sided CUSUM per feature on the
same robust z-scores (trend.py), for slow drifts that never cross the point threshold.
"""
from __future__ import annotations

import math
from datetime import timedelta

from ..timeline import Bucket, Timeline
from . import trend as trend_mod
from .classify import TREND_PATTERN, final_classification, window_pattern
from .features import BASE_FEATURES, METRIC_PREFIX, FeatureStat, fit_stat, observed, quantile
from .scoring import isolation_forest_raw, sklearn_available, statistical_raw
from .severity import EpisodeEvidence, grade
from .trend import Channel, trend_scores
from .types import AnomalyReport, Episode, WindowFinding

METHODS = ("statistical", "isolation_forest", "statistical_trend")

# Decision of the evaluation in docs/ml-evaluation.md: which candidate "auto" runs.
# statistical (dev rule vs Isolation Forest, section 5). statistical_trend stays selectable but is
# not the default: its post-review constants were fixed after the evaluation seeds had been seen
# (section 5c), so adopting it needs a fresh, untouched seed set.
AUTO_METHOD = "statistical"

MIN_TRAIN_WINDOWS = 12  # healthy windows needed to fit a baseline
EXCLUDE_Z = 6.0  # a candidate training window deviating more than this is not healthy
MIN_METRIC_COVERAGE = 0.5  # a metric becomes a feature if >= this share of training windows has it
WARMUP_FRACTION = 0.1  # new_groups ignored in the first 10 % of the training block (all types are "new")
TOP_CONTRIBUTORS = 3
CONTRIB_MIN_Z = 2.0  # a feature is listed as a contributor from 2 robust sigmas in the unusual direction

# Decision threshold = max(floor, margin x quantile of the *training* scores). Raw units:
# robust sigmas (statistical) / IsolationForest score above the training median.
STAT_FLOOR, STAT_CAL_QUANTILE, STAT_CAL_MARGIN = 4.0, 0.98, 1.0
IF_FLOOR, IF_CAL_QUANTILE, IF_CAL_MARGIN = 0.02, 0.90, 1.2
# IsolationForest is fed the unusual-direction deviations only (same evidence as the baseline);
# feeding signed z-scores measured worse on the tuning seeds (docs/ml-evaluation.md).


def _idle_finding(b: Bucket) -> WindowFinding:
    return WindowFinding(
        start=b.start, end=b.start + timedelta(seconds=b.window_seconds), score=0.0,
        is_anomalous=False, classification="normal", severity=None, features=observed(b),
    )


def _degraded(report: AnomalyReport, note: str) -> AnomalyReport:
    report.notes.append(note)
    return report


def detect_anomalies(
    timeline: Timeline, *, method: str = "auto", train_fraction: float = 0.5, seed: int = 0,
) -> AnomalyReport:
    """Score every window of `timeline` against a baseline fitted on its earlier healthy part.

    Never raises on data problems: too few windows, an all-zero timeline, or a missing
    scikit-learn produce a report with an explanatory note (and, if nothing can be fitted,
    unscored windows). Raises ValueError only for an unknown `method` or a `train_fraction`
    outside (0, 1).
    """
    if method != "auto" and method not in METHODS:
        raise ValueError(f"unknown method {method!r}; use 'auto' or one of {', '.join(METHODS)}")
    if not 0.0 < train_fraction < 1.0:
        raise ValueError("train_fraction must be strictly between 0 and 1")
    resolved = AUTO_METHOD if method == "auto" else method
    buckets = timeline.buckets
    report = AnomalyReport(
        method_used=resolved, requested_method=method,
        windows=[_idle_finding(b) for b in buckets], train_range=None,
    )
    if timeline.untimed_events:
        report.notes.append(
            f"{timeline.untimed_events} event(s) without timestamps were excluded from anomaly detection"
        )
    if resolved == "isolation_forest" and not sklearn_available():
        report.method_used = resolved = "statistical"
        report.notes.append("scikit-learn is not installed; used the statistical baseline instead")
    n = len(buckets)
    if n == 0:
        return _degraded(report, "timeline has no windows; no anomalies reported")
    if not any(b.total for b in buckets):
        return _degraded(report, "timeline contains no events; no anomalies reported")
    block = int(n * train_fraction)
    if block < MIN_TRAIN_WINDOWS:
        return _degraded(
            report,
            f"too few windows to train (need >= {MIN_TRAIN_WINDOWS} healthy windows in the first "
            f"{train_fraction:.0%} of the timeline; {n} windows give {block}); no anomalies reported",
        )

    obs = [observed(b) for b in buckets]
    warmup = max(1, math.ceil(WARMUP_FRACTION * block))
    metric_names = _select_metrics(obs[:block])
    names = list(BASE_FEATURES) + metric_names
    matrix = [[o.get(f, math.nan) for f in names] for o in obs]
    ng = names.index("new_groups")
    for row in matrix[:warmup]:
        row[ng] = math.nan  # first sightings of ordinary error types are expected at log start

    kept, stats, excluded = _select_training(matrix[:block], names)
    if len(kept) < MIN_TRAIN_WINDOWS:
        return _degraded(
            report,
            f"too few healthy windows to train: {len(kept)} of {block} remain after excluding "
            f"{len(excluded)} extreme windows (need >= {MIN_TRAIN_WINDOWS}); no anomalies reported",
        )
    report.train_range = (buckets[0].start, buckets[block - 1].start + timedelta(seconds=buckets[0].window_seconds))
    for i in kept:
        report.windows[i].used_for_training = True
    if excluded:
        report.notes.append(
            f"{len(excluded)} of the first {block} windows were extreme and excluded from the baseline"
        )
    if warmup:
        report.notes.append(f"new-error-group counts of the first {warmup} windows are ignored (log start)")

    z_rows = [[s.z(v) for s, v in zip(stats, row)] for row in matrix]
    ex_rows = [[s.excess(z) for s, z in zip(stats, zr)] for zr in z_rows]

    if resolved in ("statistical", "statistical_trend"):
        raw = statistical_raw(ex_rows)
        raw_train = [raw[i] for i in kept]
        floor, cal_q, cal_margin = STAT_FLOOR, STAT_CAL_QUANTILE, STAT_CAL_MARGIN
    else:
        raw, raw_train = isolation_forest_raw([ex_rows[i] for i in kept], ex_rows, seed)
        floor, cal_q, cal_margin = IF_FLOOR, IF_CAL_QUANTILE, IF_CAL_MARGIN
    threshold = max(floor, cal_margin * quantile(sorted(raw_train), cal_q))
    report.parameters = {
        "method": resolved, "features": names, "train_windows": len(kept), "train_block_windows": block,
        "excluded_windows": len(excluded), "threshold_raw": threshold, "seed": seed,
        "baseline": {s.name: {"median": s.median, "scale": s.scale, "direction": s.direction} for s in stats},
    }

    point = [r >= threshold for r in raw]
    trend = None
    if resolved == "statistical_trend":
        z_cols = {f: [zr[j] for zr in z_rows] for j, f in enumerate(names)}
        chans = trend_mod.channels(names, [s.direction for s in stats])
        trend = trend_scores(chans, z_cols, point, kept)
        report.parameters["trend"] = {
            "k": trend_mod.K_REF,
            "channels": {
                _channel_key(c): {"threshold": h, "mean": trend.centers[c][0], "sd": trend.centers[c][1]}
                for c, h in trend.thresholds.items()
            },
        }
        flagged = [p or t >= 1.0 for p, t in zip(point, trend.raw)]
    else:
        flagged = point
    patterns: dict[int, str] = {}
    for i, f in enumerate(flagged):
        if not f:
            continue
        patterns[i] = (
            window_pattern(dict(zip(names, ex_rows[i])), dict(zip(names, z_rows[i]))) if point[i]
            else TREND_PATTERN
        )
    runs = _runs(flagged)
    run_len = {i: len(r) for r in runs for i in r}

    stat_by_name = {s.name: s for s in stats}
    for i in range(n):
        w = report.windows[i]
        if trend is None:
            w.score = raw[i] / (raw[i] + threshold)
        else:
            r = max(raw[i] / threshold, trend.raw[i])
            if not flagged[i]:
                r = min(r, math.nextafter(1.0, 0.0))  # division rounding must not reach the 0.5 cut
            w.score = r / (r + 1.0)
        if not flagged[i]:
            continue
        w.is_anomalous = True
        w.classification = final_classification(patterns[i], run_len[i])
        w.contributions = _contributions(
            names, stat_by_name, obs[i], ex_rows[i], z_rows[i], fallback=point[i],
        )
        if trend is not None:
            _add_trend_contributions(
                w.contributions, trend, i, names, stat_by_name, obs[i], z_rows[i], first=not point[i],
            )

    for run in runs:
        ep = _episode(report, run, buckets, stat_by_name, warmup, n)
        report.episodes.append(ep)
        for i in run:
            report.windows[i].severity = ep.severity
    return report


def _select_metrics(candidates: list[dict[str, float]]) -> list[str]:
    counts: dict[str, int] = {}
    for o in candidates:
        for k in o:
            if k.startswith(METRIC_PREFIX):
                counts[k] = counts.get(k, 0) + 1
    need = MIN_METRIC_COVERAGE * len(candidates)
    return sorted(k for k, c in counts.items() if c >= need)


def _select_training(rows: list[list[float]], names: list[str]) -> tuple[list[int], list[FeatureStat], list[int]]:
    """Training windows = candidate block minus windows that are extreme under a robust rule.

    1. Fit median/MAD per feature on *all* candidate windows (median and MAD tolerate up to
       ~50 % contamination, so an incident inside the block barely moves them).
    2. Drop every window whose largest unusual-direction robust z exceeds EXCLUDE_Z.
    3. Refit the baseline on the windows that remain. No second exclusion round (no cascade).
    """
    first = [fit_stat(f, [r[j] for r in rows]) for j, f in enumerate(names)]
    extreme = [
        max(s.excess(s.z(v)) for s, v in zip(first, row)) > EXCLUDE_Z for row in rows
    ]
    kept = [i for i, e in enumerate(extreme) if not e]
    excluded = [i for i, e in enumerate(extreme) if e]
    final = [fit_stat(f, [rows[i][j] for i in kept]) for j, f in enumerate(names)]
    return kept, final, excluded


def _contributions(names, stat_by_name, obs, excess, z, *, fallback: bool = True) -> list[dict]:
    order = sorted(range(len(names)), key=lambda j: -excess[j])[:TOP_CONTRIBUTORS]
    # features that deviate noticeably; if the flag came from a combination of small
    # deviations, still name the single largest one (unless a trend alarm explains the flag)
    order = [j for j in order if excess[j] >= CONTRIB_MIN_Z] or (order[:1] if fallback else [])
    out = []
    for j in order:
        if excess[j] <= 0:
            break
        s = stat_by_name[names[j]]
        out.append({
            "feature": names[j], "observed": obs.get(names[j], math.nan),
            "baseline": s.median, "deviation": z[j],
        })
    return out


def _channel_key(c: Channel) -> str:
    return f"{c.feature}{'+' if c.sign > 0 else '-'}"


def _add_trend_contributions(contribs, trend, i, names, stat_by_name, obs, z, *, first: bool) -> None:
    """Name every feature whose CUSUM alarms in window i, with its cumulative sum and threshold.

    `cusum` / `cusum_threshold` are in robust sigmas; a feature already listed by the point
    detector gets the two keys added. `first`: drifting features go before point contributions
    (the window was flagged by the trend alone).
    """
    alarms: dict[str, tuple[float, float]] = {}  # feature -> (S, h) of its strongest alarming channel
    for ch, sums in trend.sums.items():
        h = trend.thresholds[ch]
        best = alarms.get(ch.feature)
        if sums[i] >= h and (best is None or sums[i] / h > best[0] / best[1]):
            alarms[ch.feature] = (sums[i], h)
    ranked = sorted(alarms, key=lambda f: (-alarms[f][0] / alarms[f][1], f))[:TOP_CONTRIBUTORS]
    listed = {c["feature"]: c for c in contribs}
    new = []
    for f in ranked:
        s, h = alarms[f]
        entry = listed.get(f)
        if entry is None:
            st = stat_by_name[f]
            entry = {"feature": f, "observed": obs.get(f, math.nan), "baseline": st.median,
                     "deviation": z[names.index(f)]}
            new.append(entry)
        entry["cusum"], entry["cusum_threshold"] = s, h
    contribs[:] = new + contribs if first else contribs + new


def _runs(flags: list[bool]) -> list[list[int]]:
    runs: list[list[int]] = []
    for i, f in enumerate(flags):
        if not f:
            continue
        if runs and runs[-1][-1] == i - 1:
            runs[-1].append(i)
        else:
            runs.append([i])
    return runs


def _safe_mean(vals: list[float]) -> float:
    """Mean without summing raw values (a finite mean of huge finite values must not overflow)."""
    m = 0.0
    for i, v in enumerate(vals, 1):
        m = m * ((i - 1) / i) + v / i
    return m


def _episode(report, run, buckets, stats, warmup, n) -> Episode:
    wins = [report.windows[i] for i in run]
    peak = max(wins, key=lambda w: w.score)
    base_err = stats["error_count"].median
    base_rate = stats["event_rate"].median
    span = [buckets[i] for i in run]
    ep_rate = sum(b.event_rate for b in span) / len(span)
    metric_shift, metric_name = 0.0, None
    for name, s in stats.items():
        if not name.startswith(METRIC_PREFIX) or abs(s.median) < 1e-9:
            continue
        key = name[len(METRIC_PREFIX):]
        vals = [b.metrics[key] for b in span if key in b.metrics and math.isfinite(b.metrics[key])]
        if vals:
            shift = abs(_safe_mean(vals) / s.median - 1.0)
            if shift > metric_shift:
                metric_shift, metric_name = shift, name
    evidence = EpisodeEvidence(
        window_count=len(run),
        duration_seconds=float(len(run) * span[0].window_seconds),
        ongoing=run[-1] == n - 1,
        excess_errors=sum(max(0.0, b.errors - base_err) for b in span),
        peak_error_ratio=max(b.error_ratio for b in span),
        new_groups=sum(b.new_groups for i, b in zip(run, span) if i >= warmup),
        rate_drop=max(0.0, 1.0 - ep_rate / base_rate) if base_rate > 0 else 0.0,
        metric_shift=metric_shift, metric_shift_name=metric_name,
    )
    g = grade(evidence)
    weight: dict[str, float] = {}
    for w in wins:
        for c in w.contributions:
            weight[c["feature"]] = weight.get(c["feature"], 0.0) + abs(c["deviation"])
    top = sorted(weight, key=lambda f: (-weight[f], f))[:TOP_CONTRIBUTORS]
    return Episode(
        start=wins[0].start, end=wins[-1].end, peak_score=peak.score,
        classification=peak.classification, severity=g.level, window_count=len(run),
        evidence=g.reasons, features=top,
    )
