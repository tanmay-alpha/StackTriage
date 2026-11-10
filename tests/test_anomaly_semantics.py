"""Three separate quantities: anomaly score, anomaly classification, operational severity."""
from __future__ import annotations

import inspect

import pytest
from anomaly_helpers import build, patch_range, series

from stacktriage.anomaly import EpisodeEvidence, detect_anomalies, grade
from stacktriage.anomaly.classify import DEV_Z, SUSTAINED_MIN_WINDOWS, final_classification, window_pattern

RANK = {"low": 0, "medium": 1, "high": 2, "critical": 3}
N = 100  # first 50 windows are the baseline


def run(rows, **kw):
    return detect_anomalies(build(rows), method="statistical", **kw)


# ---- classification rules ----------------------------------------------------------------------

def test_pattern_rules_on_explicit_deviations():
    z = {"error_count": 0.0, "error_ratio": 0.0, "new_groups": 0.0, "event_rate": 0.0}
    ex = dict(z)
    assert window_pattern(ex, z) == "unusual_pattern"  # flagged but no single feature deviates

    ex = {**z, "new_groups": DEV_Z + 1}
    assert window_pattern(ex, {**z, "new_groups": DEV_Z + 1}) == "new_error_type"

    ex = {**z, "error_count": 9.0}
    assert window_pattern(ex, {**z, "error_count": 9.0}) == "deviation"
    ex = {**z, "new_groups": 6.0, "error_count": 9.0}
    assert window_pattern(ex, {**z, "new_groups": 6.0, "error_count": 9.0}) == "deviation"  # not novelty alone

    ex = {**z, "event_rate": 8.0}  # excess is the unusual-direction amount
    assert window_pattern(ex, {**z, "event_rate": -8.0}) == "quiet_period"
    ex = {**z, "event_rate": 8.0, "error_count": 5.0}  # traffic loss with an error surge is not "quiet"
    assert window_pattern(ex, {**z, "event_rate": -8.0, "error_count": 5.0}) == "deviation"

    ex = {**z, "metric.latency_ms": 6.0}
    assert window_pattern(ex, {**z, "metric.latency_ms": 6.0}) == "deviation"


def test_duration_splits_spike_from_sustained_shift():
    assert final_classification("deviation", 1) == "spike"
    assert final_classification("deviation", SUSTAINED_MIN_WINDOWS - 1) == "spike"
    assert final_classification("deviation", SUSTAINED_MIN_WINDOWS) == "sustained_shift"
    for p in ("quiet_period", "new_error_type", "unusual_pattern"):
        assert final_classification(p, 10) == p and final_classification(p, 1) == p


def test_single_window_error_burst_is_a_spike():
    rows = series(N, seed=1)
    patch_range(rows, 70, 71, errors=30)
    rep = run(rows)
    w = rep.windows[70]
    assert w.is_anomalous and w.classification == "spike"
    assert w.contributions[0]["feature"] in ("error_count", "error_ratio") and w.contributions[0]["deviation"] > 0
    assert [e.classification for e in rep.episodes] == ["spike"]


def test_persistent_error_level_is_a_sustained_shift():
    rows = series(N, seed=1)
    patch_range(rows, 70, 78, errors=30)
    rep = run(rows)
    assert {rep.windows[i].classification for i in range(70, 78)} == {"sustained_shift"}
    assert rep.episodes[0].classification == "sustained_shift" and rep.episodes[0].window_count == 8


def test_traffic_drop_without_errors_is_a_quiet_period():
    rows = series(N, seed=2)
    patch_range(rows, 70, 76, total=4, errors=0)  # ERROR count is *below* normal
    rep = run(rows)
    cls = {rep.windows[i].classification for i in range(70, 76) if rep.windows[i].is_anomalous}
    assert cls == {"quiet_period"} and sum(rep.windows[i].is_anomalous for i in range(70, 76)) >= 5
    top = rep.windows[72].contributions[0]
    assert top["feature"] == "event_rate" and top["deviation"] < 0 and top["observed"] < top["baseline"]


def test_one_new_error_group_at_normal_volume_is_new_error_type():
    rows = series(N, seed=3)
    rows[70]["new_groups"] = 1
    rep = run(rows)
    w = rep.windows[70]
    assert w.is_anomalous and w.classification == "new_error_type"
    assert [c["feature"] for c in w.contributions] == ["new_groups"]


def test_latency_rise_carried_only_in_metrics_is_found_and_attributed():
    rows = series(N, seed=4)
    for i in range(70, 80):
        rows[i]["metrics"] = {**rows[i]["metrics"], "latency_ms": 135.0 + (i % 3)}
    rep = run(rows)
    hit = [i for i in range(70, 80) if rep.windows[i].is_anomalous]
    assert len(hit) >= 8
    assert all(rep.windows[i].classification == "sustained_shift" for i in hit)
    assert rep.windows[75].contributions[0]["feature"] == "metric.latency_ms"
    assert all(rows[i]["errors"] <= 3 for i in range(70, 80))  # log levels alone show nothing


def test_busy_but_healthy_volume_is_not_flagged():
    rows = series(N, seed=5)
    patch_range(rows, 70, 90, total=130)  # 2.6x traffic, same error count
    rep = run(rows)
    assert not any(w.is_anomalous for w in rep.windows[70:90])


def test_normal_window_has_no_class_severity_or_contributions():
    rep = run(series(N, seed=6))
    w = rep.windows[10]
    assert (w.classification, w.severity, w.contributions) == ("normal", None, [])


# ---- severity: separate from the score -----------------------------------------------------------

def test_grade_takes_evidence_only():
    assert list(inspect.signature(grade).parameters) == ["ev"]
    fields = set(EpisodeEvidence.__dataclass_fields__)
    assert not any("score" in f for f in fields)


def _ev(**kw):
    base = dict(window_count=1, duration_seconds=60.0, ongoing=False, excess_errors=0.0,
                peak_error_ratio=0.0, new_groups=0, rate_drop=0.0, metric_shift=0.0)
    base.update(kw)
    return EpisodeEvidence(**base)


def test_severity_follows_evidence_levels():
    assert grade(_ev()).level == "low"
    assert grade(_ev(peak_error_ratio=0.25, excess_errors=60)).level == "high"
    assert grade(_ev(duration_seconds=3600, ongoing=True, peak_error_ratio=0.6, excess_errors=500,
                     new_groups=3)).level == "critical"
    # each piece of evidence moves the grade on its own
    base = grade(_ev(peak_error_ratio=0.12)).points
    assert grade(_ev(peak_error_ratio=0.12, ongoing=True)).points == base + 1
    assert grade(_ev(peak_error_ratio=0.12, new_groups=1)).points == base + 1
    assert grade(_ev(peak_error_ratio=0.12, duration_seconds=3600)).points == base + 2
    assert grade(_ev(rate_drop=0.9)).level == "medium"
    assert grade(_ev(metric_shift=1.2, metric_shift_name="metric.latency_ms")).reasons[0].startswith("metric")
    assert grade(_ev()).reasons  # always explains itself


def test_same_evidence_same_severity_regardless_of_anything_else():
    a = grade(_ev(peak_error_ratio=0.3, excess_errors=80, duration_seconds=900))
    b = grade(_ev(peak_error_ratio=0.3, excess_errors=80, duration_seconds=900))
    assert a == b


def test_higher_score_can_mean_lower_severity_in_one_report():
    """Counterexample: a brief, extreme blip outranks a milder but sustained, still-running problem."""
    rows = series(N, seed=8)
    patch_range(rows, 60, 61, errors=14, total=60)  # one very unusual window, brief and over
    patch_range(rows, 88, N, errors=7, total=57)  # mildly unusual, 12 windows, still ongoing at the end
    rep = run(rows)
    assert len(rep.episodes) == 2
    blip, ongoing = rep.episodes
    assert blip.window_count == 1 and ongoing.window_count >= 8
    assert blip.peak_score > ongoing.peak_score
    assert RANK[blip.severity] < RANK[ongoing.severity]
    assert any("still ongoing" in x for x in ongoing.evidence)


def test_equal_scores_can_have_different_severity():
    """Same peak score (same single feature deviation), different operational evidence."""
    rows_a = series(N, seed=9)
    rows_b = [dict(r) for r in rows_a]
    patch_range(rows_a, 60, 61, errors=12, total=50)
    patch_range(rows_b, 60, 61, errors=12, total=50)
    patch_range(rows_b, 61, 70, errors=12, total=50)  # same peak window, but lasts 10 windows
    a, b = run(rows_a), run(rows_b)
    assert a.episodes[0].peak_score == pytest.approx(b.episodes[0].peak_score)
    assert RANK[b.episodes[0].severity] > RANK[a.episodes[0].severity]


def test_severity_is_not_everything_critical():
    """The upstream bug: every row came out CRITICAL because severity was a function of the score."""
    rows = series(N, seed=10)
    patch_range(rows, 60, 61, errors=9)
    patch_range(rows, 75, 95, errors=40, total=90, new_groups=0)
    rows[75]["new_groups"] = 3
    rep = run(rows)
    sev = {e.severity for e in rep.episodes}
    assert len(sev) >= 2 and "critical" not in {rep.episodes[0].severity}
    for e in rep.episodes:
        assert e.evidence and all(isinstance(x, str) for x in e.evidence)


def test_episode_lists_contributing_features_but_no_cause():
    rows = series(N, seed=11)
    patch_range(rows, 70, 76, errors=25)
    ep = run(rows).episodes[0]
    assert "error_count" in ep.features
    assert not hasattr(ep, "root_cause") and not hasattr(ep, "cause")


def _metric_episode(latencies):
    rows = series(N, seed=12)
    patch_range(rows, 70, 70 + len(latencies), errors=30)
    for i, v in enumerate(latencies):
        rows[70 + i]["metrics"] = {**rows[70 + i]["metrics"], "latency_ms": v}
    return run(rows).episodes[0]


def test_severity_ignores_non_finite_metric_values():
    nan = float("nan")
    base = _metric_episode([80.0, 80.0, 80.0, 80.0, 80.0])
    assert not any("shifted" in x for x in base.evidence)
    # a NaN window must not suppress a real ~110 % latency shift in the other windows
    shifted = _metric_episode([170.0, 170.0, 170.0, 170.0, nan])
    assert any("metric.latency_ms shifted" in x and "(+2)" in x for x in shifted.evidence)
    # +inf must not add spurious points to an otherwise unshifted episode
    with_inf = _metric_episode([80.0, 80.0, 80.0, 80.0, float("inf")])
    assert not any("shifted" in x for x in with_inf.evidence)
    assert with_inf.severity == base.severity
