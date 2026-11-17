"""Trend candidate (`statistical_trend`): CUSUM math, training-only calibration, the default chosen
by the pre-registered rule, and the regression guarantee that `method="statistical"` is unchanged."""
from __future__ import annotations

import hashlib
import json
import random

import pytest
from anomaly_helpers import build, series
from make_ml_scenarios import make_scenario

from stacktriage.anomaly import AUTO_METHOD, detect_anomalies
from stacktriage.anomaly import trend as trend_mod
from stacktriage.anomaly.classify import TREND_PATTERN, final_classification

# ---- CUSUM math ------------------------------------------------------------------------------


def test_cusum_known_sequence_gives_known_alarm_index():
    xs = [0.0, 0.0, 2.0, 2.0, 2.0, 0.0]
    sums = trend_mod.cusum(xs, 0.5)
    assert sums == [0.0, 0.0, 1.5, 3.0, 4.5, 4.0]
    assert trend_mod.first_alarm(sums, 4.0) == 4
    assert trend_mod.first_alarm(sums, 5.0) is None


def test_cusum_never_goes_negative_and_resets_on_low_values():
    assert trend_mod.cusum([3.0, -10.0, 1.0], 1.0) == [2.0, 0.0, 0.0]


def test_cusum_hold_and_cap():
    xs = [0.0, 0.0, 2.0, 2.0, 2.0, 0.0]
    held = trend_mod.cusum(xs, 0.5, hold=[False, False, False, True, False, False])
    assert held == [0.0, 0.0, 1.5, 1.5, 3.0, 2.5]  # a held step leaves S unchanged
    assert trend_mod.cusum(xs, 0.5, cap=3.0) == [0.0, 0.0, 1.5, 3.0, 3.0, 2.5]


def test_no_alarm_on_flat_noise():
    # bounded noise below k can never accumulate
    rng = random.Random(1)
    assert max(trend_mod.cusum([rng.uniform(-1.0, 1.0) for _ in range(2000)], 1.0)) == 0.0
    # unit Gaussian noise, the chosen k and h: no alarm in 5 x 1000 windows
    for seed in range(5):
        rng = random.Random(seed)
        sums = trend_mod.cusum([rng.gauss(0.0, 1.0) for _ in range(1000)], trend_mod.K_REF)
        assert trend_mod.first_alarm(sums, trend_mod.H_FLOOR) is None


def test_constant_drift_alarms_after_the_expected_number_of_steps():
    # x = k + 1 per step -> S grows by exactly 1 per step: first alarm when S reaches h
    k, h = 1.0, 5.0
    assert trend_mod.first_alarm(trend_mod.cusum([k + 1.0] * 20, k), h) == 4


def test_standardizer_and_calibration_floor():
    assert trend_mod.standardizer([]) == (0.0, 1.0)
    mu, sd = trend_mod.standardizer([2.0, 2.0, 2.0])
    assert mu == 2.0 and sd == trend_mod.SD_FLOOR  # flat series is not amplified
    assert trend_mod.calibrate_h([0.0] * 30, [False] * 30, 1.0) == trend_mod.H_FLOOR
    drift = [2.0] * 10  # S reaches 10 on the training windows -> h = margin x 10
    assert trend_mod.calibrate_h(drift, [False] * 10, 1.0) == max(trend_mod.H_FLOOR, trend_mod.H_MARGIN * 10.0)


def test_channels_cover_error_features_upward_and_metrics_both_ways():
    names = ["error_count", "event_rate", "error_ratio", "new_groups", "metric.latency_ms"]
    chans = trend_mod.channels(names, [1, -1, 1, 1, 0])
    assert [(c.feature, c.sign) for c in chans] == [
        ("error_count", 1), ("error_ratio", 1), ("metric.latency_ms", 1), ("metric.latency_ms", -1),
    ]


# ---- calibration uses training windows only -------------------------------------------------------


def test_calibration_sees_only_the_kept_training_windows(monkeypatch):
    sc = make_scenario("noisy_mixed", 3)
    seen: list[int] = []
    real = trend_mod.calibrate_h

    def spy(train_xs, train_hold, k):
        seen.append(len(train_xs))
        return real(train_xs, train_hold, k)

    monkeypatch.setattr(trend_mod, "calibrate_h", spy)
    rep = detect_anomalies(sc.timeline, method="statistical_trend", seed=3)
    block = int(len(sc.timeline.buckets) * 0.5)
    kept = [i for i, w in enumerate(rep.windows) if w.used_for_training]
    assert kept and max(kept) < block
    assert seen and all(n == len(kept) for n in seen)
    assert len(seen) == len(rep.parameters["trend"]["channels"])


def test_trend_parameters_and_training_scores_do_not_depend_on_the_later_period():
    sc = make_scenario("noisy_mixed", 3)
    tl = sc.timeline
    b = int(len(tl.buckets) * 0.5)
    base = detect_anomalies(tl, method="statistical_trend", seed=3)
    rows = [
        {"total": x.total, "errors": x.errors, "new_groups": x.new_groups, "metrics": dict(x.metrics)}
        for x in tl.buckets
    ]
    for r in rows[b:]:
        r.update(total=r["total"] * 9 + 1, errors=r["errors"] * 7 + 20, new_groups=5,
                 metrics={k: v * 4 for k, v in r["metrics"].items()})
    other = detect_anomalies(build(rows), method="statistical_trend", seed=3)
    assert base.parameters["trend"] == other.parameters["trend"]
    assert [w.score for w in base.windows[:b]] == [w.score for w in other.windows[:b]]


# ---- behaviour on a slow drift ---------------------------------------------------------------------


def _ramp_rows(seed: int, start: int = 70, n: int = 120, slope: float = 0.25) -> list[dict]:
    """Healthy rows; latency_ms rises by `slope` ms per window from `start` (never 4 sigma per window early on)."""
    rows = series(n, seed=seed)
    for i in range(start, n):
        m = rows[i]["metrics"]
        rows[i]["metrics"] = {**m, "latency_ms": m["latency_ms"] + slope * (i - start)}
    return rows


@pytest.mark.parametrize("seed", (1, 3))
def test_slow_metric_drift_is_found_by_the_trend_and_explained(seed):
    tl = build(_ramp_rows(seed))
    stat = detect_anomalies(tl, method="statistical")
    trend = detect_anomalies(tl, method="statistical_trend")
    assert not any(w.is_anomalous for w in stat.windows[70:])  # the point detector misses the ramp
    flagged = [i for i, w in enumerate(trend.windows) if w.is_anomalous and i >= 70]
    assert flagged and flagged[0] < 105
    for i in flagged:
        w = trend.windows[i]
        assert w.classification == "sustained_shift"
        top = w.contributions[0]
        assert top["feature"] == "metric.latency_ms" and top["deviation"] > 0
        assert top["cusum"] >= top["cusum_threshold"]
    assert "metric.latency_ms" in trend.episodes[-1].features


def test_trend_flags_are_a_superset_of_statistical_flags():
    for name, seed in (("noisy_mixed", 3), ("slow_ramp", 0), ("natural_bursts", 2)):
        tl = make_scenario(name, seed).timeline
        stat = detect_anomalies(tl, method="statistical", seed=seed)
        trend = detect_anomalies(tl, method="statistical_trend", seed=seed)
        assert all(t.is_anomalous for s, t in zip(stat.windows, trend.windows) if s.is_anomalous)
        assert all(w.is_anomalous == (w.score >= 0.5) for w in trend.windows)


def test_healthy_noise_has_no_trend_alarm():
    for seed in range(5):
        tl = build(series(120, seed=100 + seed))
        stat = detect_anomalies(tl, method="statistical")
        trend = detect_anomalies(tl, method="statistical_trend")
        assert [w.is_anomalous for w in trend.windows] == [w.is_anomalous for w in stat.windows]


def test_drift_pattern_is_always_a_sustained_shift():
    assert final_classification(TREND_PATTERN, 1) == "sustained_shift"
    assert final_classification("deviation", 1) == "spike"


# ---- regression: default and `statistical` unchanged -----------------------------------------------

# sha256 of json.dumps(report.to_dict(), sort_keys=True) for `statistical`, recorded on main before the
# trend candidate was added.
STATISTICAL_FINGERPRINTS = {
    ("slow_ramp", 0): "602df9a9b3ba03432fdc9948aec5f1e995cb9539d909908b8f442f2715622450",
    ("noisy_mixed", 3): "67f29f3bf477e16a0a099ad1840b7cbd109a2f1e14e26cb9811749d4f0e6a248",
    ("early_incident", 1): "41f8c83d552641caed0a79906b8aaedde868a2972c9fb09cb2db8f04daed6e19",
}


def test_default_method_follows_the_pre_registered_rule():
    # outcome of the rule in docs/ml-evaluation.md section 5b on evaluation seeds 0-9
    assert AUTO_METHOD == "statistical_trend"
    tl = make_scenario("slow_ramp", 4).timeline
    auto = detect_anomalies(tl, seed=4)
    assert auto.method_used == "statistical_trend" and "trend" in auto.parameters


@pytest.mark.parametrize(("name", "seed"), sorted(STATISTICAL_FINGERPRINTS))
def test_statistical_reports_are_bit_for_bit_unchanged(name, seed):
    rep = detect_anomalies(make_scenario(name, seed).timeline, method="statistical", seed=seed)
    digest = hashlib.sha256(json.dumps(rep.to_dict(), sort_keys=True).encode()).hexdigest()
    assert digest == STATISTICAL_FINGERPRINTS[(name, seed)]
    assert "trend" not in rep.parameters
