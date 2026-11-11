"""Regression coverage for synthetic evaluation isolation and numeric edge cases."""
from __future__ import annotations

import math

import pytest
from anomaly_helpers import build, series
from evaluate_anomaly import Run, _incident_stats, _prf, main, run_error_rule
from make_ml_scenarios import make_scenario

from stacktriage import Level, LogEvent, analyze
from stacktriage.anomaly import detect_anomalies
from stacktriage.anomaly.features import fit_stat


@pytest.mark.parametrize("arguments", (
    ["--dev", "--seeds", "0"],
    ["--seeds", "1000"],
    ["--seeds", ""],
    ["--seeds", "3,3"],
    ["--seeds", "invalid"],
))
def test_invalid_seed_partitions_are_rejected(monkeypatch, arguments):
    monkeypatch.setattr("sys.argv", ["evaluate_anomaly.py", *arguments])
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 2


@pytest.mark.parametrize("value", (1.7e308, -1.7e308))
def test_huge_constant_metric_has_finite_baseline_and_zero_deviation(value):
    stat = fit_stat("metric.m", [value] * 12)
    assert stat.median == value
    assert math.isfinite(stat.scale)
    assert stat.z(value) == 0.0
    rows = [{"total": 50, "errors": 1, "metrics": {"m": value}} for _ in range(60)]
    report = detect_anomalies(build(rows), method="statistical")
    assert not any(w.is_anomalous for w in report.windows)
    assert all(w.score == 0.0 for w in report.windows)


def test_huge_opposite_metric_values_have_finite_scale_and_accurate_deviation():
    stat = fit_stat("metric.m", [-1.7e308] * 7 + [1.7e308] * 5)
    assert math.isfinite(stat.median) and math.isfinite(stat.scale)
    assert stat.z(1.7e308) == pytest.approx(40.0)
    spread = fit_stat("metric.m", [-1.7e308, 1.7e308] * 6)
    assert spread.median == 0.0 and math.isfinite(spread.scale)
    assert 0.0 < spread.z(1.7e308) < 2.0


def test_rule_is_deterministic_and_fits_only_the_training_period():
    sc = make_scenario("error_spike", 3)
    before = run_error_rule(sc)
    assert before == run_error_rule(sc)
    for bucket in sc.timeline.buckets[sc.eval_from:]:
        bucket.error += 1000
        bucket.total += 1000
    after = run_error_rule(sc)
    assert after.full_flags[:sc.eval_from] == before.full_flags[:sc.eval_from]
    assert all(after.flags)



def test_untimed_events_are_excluded_and_reported():
    from datetime import UTC, datetime, timedelta

    start = datetime(2026, 3, 5, tzinfo=UTC)
    timed = [LogEvent(str(i), i + 1, "test", start + timedelta(minutes=i), Level.INFO,
                      "svc", None, "heartbeat") for i in range(40)]
    untimed = [LogEvent("error", 41, "test", None, Level.ERROR, "svc", None, "failure")]
    healthy = detect_anomalies(analyze(timed, window_seconds=60).timeline, method="statistical")
    mixed = detect_anomalies(analyze(timed + untimed, window_seconds=60).timeline, method="statistical")
    assert mixed.parameters == healthy.parameters
    assert mixed.windows == healthy.windows
    assert any("1 event" in note and "timestamp" in note for note in mixed.notes)
    missing = detect_anomalies(analyze(untimed, window_seconds=60).timeline, method="statistical")
    assert missing.windows == [] and missing.episodes == []
    assert any("timestamp" in note for note in missing.notes)


@pytest.mark.parametrize("method", ("statistical", "isolation_forest"))
def test_scores_respect_the_fitted_threshold_on_small_training_sets(method):
    rows = series(26, seed=7)
    rows[24].update(errors=40, total=80)
    report = detect_anomalies(build(rows), method=method, seed=7)
    assert report.parameters["train_windows"] >= 12
    assert all(w.is_anomalous == (w.score >= 0.5) for w in report.windows)


def test_evaluation_metrics_count_false_alarms_and_exclude_misses_from_delay():
    labels = [False, True, True, False, False, True, True]
    flags = [False, False, True, False, True, False, False]
    quality = _prf(labels, flags)
    assert quality["precision"] == 0.5
    assert quality["recall"] == 0.25
    assert quality["f1"] == pytest.approx(1 / 3)
    assert quality["fpr"] == pytest.approx(1 / 3)
    run = Run("mixed", "main", 0, labels, flags, [float(f) for f in flags], [(1, 3), (5, 7)], 2)
    incidents = _incident_stats([run])
    assert incidents["inc_recall"] == 0.5
    assert incidents["delay"] == 1.0
    assert incidents["false_eps"] == 1
