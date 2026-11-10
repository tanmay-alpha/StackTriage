"""Window anomaly detection: contract, temporal split, degradation, contamination, determinism."""
from __future__ import annotations

import json
import math
import re
from datetime import timedelta
from pathlib import Path

import pytest
from anomaly_helpers import W, build, patch_range, series
from make_ml_scenarios import make_scenario

from stacktriage.anomaly import AUTO_METHOD, METHODS, detect_anomalies
from stacktriage.anomaly import detect as detect_mod
from stacktriage.timeline import Timeline

SCENARIOS_FOR_SPLIT = ("error_spike", "traffic_drop", "latency_rise", "new_error_type", "noisy_mixed")


def _block(tl: Timeline, frac: float = 0.5) -> int:
    return int(len(tl.buckets) * frac)


# ---- contract -------------------------------------------------------------------------------

@pytest.mark.parametrize("method", METHODS)
def test_report_shape_and_invariants(method):
    sc = make_scenario("noisy_mixed", 0)
    rep = detect_anomalies(sc.timeline, method=method, seed=0)
    assert rep.method_used == method and rep.requested_method == method
    assert len(rep.windows) == len(sc.timeline.buckets)
    for w, b in zip(rep.windows, sc.timeline.buckets):
        assert w.start == b.start and w.end == b.start + timedelta(seconds=b.window_seconds)
        assert 0.0 <= w.score <= 1.0
        assert w.is_anomalous == (w.score >= 0.5)  # 0.5 is the fitted decision threshold
        assert (w.severity is None) == (not w.is_anomalous)
        assert (w.classification == "normal") == (not w.is_anomalous)
        assert (not w.is_anomalous) <= (w.contributions == [])
        for c in w.contributions:
            assert set(c) == {"feature", "observed", "baseline", "deviation"}
    flagged = [i for i, w in enumerate(rep.windows) if w.is_anomalous]
    assert flagged, "noisy_mixed contains four labelled incidents"
    covered = sorted(i for e in rep.episodes for i in range(
        int((e.start - rep.windows[0].start).total_seconds() // W),
        int((e.start - rep.windows[0].start).total_seconds() // W) + e.window_count))
    assert covered == flagged  # episodes = maximal runs of consecutive anomalous windows
    for e in rep.episodes:
        in_ep = [w for w in rep.windows if e.start <= w.start < e.end]
        assert len(in_ep) == e.window_count and all(w.is_anomalous for w in in_ep)
        assert e.peak_score == max(w.score for w in in_ep)
        assert {w.severity for w in in_ep} == {e.severity}
    json.dumps(rep.to_dict())  # plain data only


def test_auto_uses_the_documented_default():
    sc = make_scenario("error_spike", 0)
    auto = detect_anomalies(sc.timeline, method="auto", seed=0)
    explicit = detect_anomalies(sc.timeline, method=AUTO_METHOD, seed=0)
    assert auto.requested_method == "auto" and auto.method_used == AUTO_METHOD
    assert [w.score for w in auto.windows] == [w.score for w in explicit.windows]
    assert AUTO_METHOD in METHODS


def test_invalid_arguments_are_rejected():
    tl = build(series(40))
    with pytest.raises(ValueError):
        detect_anomalies(tl, method="deep_learning")
    for bad in (0.0, 1.0, -0.1, 1.5):
        with pytest.raises(ValueError):
            detect_anomalies(tl, train_fraction=bad)


def test_no_pickle_or_joblib_anywhere_in_the_package():
    root = Path(__file__).resolve().parent.parent / "src" / "stacktriage" / "anomaly"
    for f in root.glob("*.py"):
        code = re.sub(r'""".*?"""', "", f.read_text(encoding="utf-8"), flags=re.S)
        code = "\n".join(line.split("#")[0] for line in code.splitlines())
        assert not re.search(r"\b(pickle|joblib|dill|cloudpickle|marshal)\b", code), f.name


# ---- temporal split: no leakage ---------------------------------------------------------------

@pytest.mark.parametrize("name", SCENARIOS_FOR_SPLIT)
@pytest.mark.parametrize("seed", (0, 1))
def test_training_range_strictly_precedes_every_incident_window(name, seed):
    sc = make_scenario(name, seed)
    rep = detect_anomalies(sc.timeline, method="statistical", seed=seed)
    b = _block(sc.timeline)
    assert rep.train_range is not None
    assert rep.train_range[0] == sc.timeline.buckets[0].start
    assert rep.train_range[1] <= sc.timeline.buckets[b].start
    assert sc.incidents and all(sc.timeline.buckets[i.start].start >= rep.train_range[1] for i in sc.incidents)
    labelled = [i for i, lab in enumerate(sc.labels) if lab]
    assert labelled and min(labelled) >= b
    assert not any(w.used_for_training for w in rep.windows[b:])
    assert any(w.used_for_training for w in rep.windows[:b])


@pytest.mark.parametrize("method", METHODS)
def test_baseline_and_threshold_do_not_depend_on_the_later_period(method):
    sc = make_scenario("noisy_mixed", 3)
    tl = sc.timeline
    b = _block(tl)
    base = detect_anomalies(tl, method=method, seed=3)
    # replace every later window by something wildly different
    rows = [
        {"total": x.total, "errors": x.errors, "new_groups": x.new_groups, "metrics": dict(x.metrics)}
        for x in tl.buckets
    ]
    for r in rows[b:]:
        r.update(total=r["total"] * 9 + 1, errors=r["errors"] * 7 + 20, new_groups=5,
                 metrics={k: v * 4 for k, v in r["metrics"].items()})
    other = detect_anomalies(build(rows), method=method, seed=3)
    assert base.parameters["baseline"] == other.parameters["baseline"]
    assert base.parameters["threshold_raw"] == other.parameters["threshold_raw"]
    assert [w.score for w in base.windows[:b]] == [w.score for w in other.windows[:b]]


def test_statistical_scores_never_peek_at_later_windows():
    rows = series(80, seed=4)
    rep_a = detect_anomalies(build(rows), method="statistical")
    rows_b = [dict(r) for r in rows]
    patch_range(rows_b, 60, 80, errors=40)
    rep_b = detect_anomalies(build(rows_b), method="statistical")
    assert [w.score for w in rep_a.windows[:60]] == [w.score for w in rep_b.windows[:60]]
    assert any(w.is_anomalous for w in rep_b.windows[60:]) and not any(w.is_anomalous for w in rep_a.windows[60:])


# ---- degrade safely ------------------------------------------------------------------------------

@pytest.mark.parametrize("method", ("auto", *METHODS))
def test_empty_timeline(method):
    rep = detect_anomalies(Timeline(60, None, None, []), method=method)
    assert rep.windows == [] and rep.episodes == [] and rep.train_range is None
    assert rep.notes and "no windows" in rep.notes[0]


@pytest.mark.parametrize("method", ("auto", *METHODS))
def test_all_zero_timeline(method):
    tl = build([{"total": 0, "errors": 0} for _ in range(60)])
    rep = detect_anomalies(tl, method=method)
    assert len(rep.windows) == 60 and rep.episodes == [] and rep.train_range is None
    assert not any(w.is_anomalous for w in rep.windows) and all(w.score == 0.0 for w in rep.windows)
    assert rep.notes and "no events" in rep.notes[0]


@pytest.mark.parametrize("method", ("auto", *METHODS))
@pytest.mark.parametrize("n", (1, 5, 23))
def test_too_few_windows_gives_note_not_exception(method, n):
    rows = series(n)
    patch_range(rows, n // 2, n, errors=50)  # even an obvious spike is not reported without a baseline
    rep = detect_anomalies(build(rows), method=method)
    assert len(rep.windows) == n and rep.episodes == [] and rep.train_range is None
    assert not any(w.is_anomalous for w in rep.windows)
    assert any("too few windows to train" in x and "no anomalies reported" in x for x in rep.notes)


def test_minimum_window_count_is_enough():
    n = int(detect_mod.MIN_TRAIN_WINDOWS / 0.5) + 2
    rows = series(n)
    patch_range(rows, n - 4, n, errors=60)
    rep = detect_anomalies(build(rows), method="statistical")
    assert rep.train_range is not None
    assert any(w.is_anomalous for w in rep.windows[n - 4:])


def test_constant_timeline_has_no_findings_and_no_division_by_zero():
    tl = build([{"total": 50, "errors": 1, "metrics": {"latency_ms": 80.0}} for _ in range(60)])
    for method in METHODS:
        rep = detect_anomalies(tl, method=method)
        assert not any(w.is_anomalous for w in rep.windows)
        assert all(math.isfinite(w.score) for w in rep.windows)


def test_non_finite_and_sparse_metrics_do_not_crash():
    rows = series(60)
    for i in range(0, 60, 2):
        rows[i]["metrics"] = {"latency_ms": math.nan, "cpu_pct": math.inf}
    for i in (40, 41):
        rows[i]["metrics"] = {}
    rep = detect_anomalies(build(rows), method="statistical")
    assert len(rep.windows) == 60
    assert all(math.isfinite(w.score) for w in rep.windows)


def test_missing_scikit_learn_falls_back_with_a_note(monkeypatch):
    monkeypatch.setattr(detect_mod, "sklearn_available", lambda: False)
    rep = detect_anomalies(build(series(60)), method="isolation_forest")
    assert rep.requested_method == "isolation_forest" and rep.method_used == "statistical"
    assert any("scikit-learn" in x for x in rep.notes)


def test_statistical_path_does_not_import_scikit_learn():
    import subprocess
    import sys

    code = (
        "import sys; sys.path[:0] = [r'%s', r'%s']\n"
        "from anomaly_helpers import build, series\n"
        "from stacktriage.anomaly import detect_anomalies\n"
        "detect_anomalies(build(series(60)), method='statistical')\n"
        "assert 'sklearn' not in sys.modules and 'numpy' not in sys.modules\n"
    ) % (Path(__file__).resolve().parent.parent / "src", Path(__file__).resolve().parent)
    subprocess.run([sys.executable, "-c", code], check=True)


# ---- contamination of the training period --------------------------------------------------------

def test_early_incident_does_not_poison_the_baseline():
    rows = series(120, seed=7)
    patch_range(rows, 8, 22, errors=45, total=95)  # incident inside the first (training) half
    tl = build(rows)
    rep = detect_anomalies(tl, method="statistical")
    incident = range(8, 22)
    assert not any(rep.windows[i].used_for_training for i in incident)
    assert sum(rep.windows[i].is_anomalous for i in incident) >= 12  # still detected, not learned as normal
    assert not any(w.is_anomalous for w in rep.windows[60:])  # healthy later period stays quiet
    assert rep.parameters["excluded_windows"] >= 12
    assert any("excluded from the baseline" in x for x in rep.notes)
    assert rep.parameters["baseline"]["error_count"]["median"] <= 2.0


def test_without_the_exclusion_rule_the_same_incident_is_missed(monkeypatch):
    """Control for the test above: disabling the rule lets the incident leak into the baseline."""
    rows = series(120, seed=7)
    for i in range(8, 34):  # 26 of 60 candidate windows: under the 50 % breakdown point
        rows[i].update(errors=30 + (i * 7) % 25, total=95)
    tl = build(rows)
    monkeypatch.setattr(detect_mod, "EXCLUDE_Z", 1e9)
    naive = detect_anomalies(tl, method="statistical")
    monkeypatch.undo()
    guarded = detect_anomalies(tl, method="statistical")
    n_naive = sum(naive.windows[i].is_anomalous for i in range(8, 34))
    n_guarded = sum(guarded.windows[i].is_anomalous for i in range(8, 34))
    assert n_guarded > n_naive
    assert naive.parameters["threshold_raw"] > guarded.parameters["threshold_raw"]  # calibrated on poisoned scores


def test_generated_early_incident_scenario_excludes_its_windows():
    sc = make_scenario("early_incident", 1)
    rep = detect_anomalies(sc.timeline, method="statistical", seed=1)
    inc = sc.incidents[0]
    in_train = [i for i in range(inc.start, inc.end) if rep.windows[i].used_for_training]
    assert len(in_train) <= (inc.end - inc.start) * 0.25
    flagged = sum(rep.windows[i].is_anomalous for i in range(inc.start, inc.end))
    assert flagged >= (inc.end - inc.start) * 0.6


def test_log_start_novelty_is_not_an_anomaly():
    rows = series(80, seed=2)
    rows[0]["new_groups"] = 4  # every error type is "new" in the first windows of any log
    rows[1]["new_groups"] = 2
    rep = detect_anomalies(build(rows), method="statistical")
    assert not any(w.is_anomalous for w in rep.windows[:2])
    rows[50]["new_groups"] = 2
    assert detect_anomalies(build(rows), method="statistical").windows[50].is_anomalous


# ---- determinism ---------------------------------------------------------------------------------------

@pytest.mark.parametrize("method", METHODS)
def test_deterministic_given_seed(method):
    sc = make_scenario("noisy_mixed", 5)
    a = detect_anomalies(sc.timeline, method=method, seed=11).to_dict()
    b = detect_anomalies(sc.timeline, method=method, seed=11).to_dict()
    assert a == b
    again = make_scenario("noisy_mixed", 5)
    assert detect_anomalies(again.timeline, method=method, seed=11).to_dict() == a


def test_detection_does_not_mutate_the_timeline():
    import copy

    sc = make_scenario("latency_rise", 2)
    before = copy.deepcopy(sc.timeline)
    detect_anomalies(sc.timeline, method="statistical")
    assert sc.timeline == before


def test_random_noise_timelines_are_quiet_in_the_later_period():
    """Pure healthy noise: at most a couple of stray windows, never a long run."""
    flagged = 0
    for seed in range(5):
        rep = detect_anomalies(build(series(120, seed=100 + seed)), method="statistical")
        flagged += sum(w.is_anomalous for w in rep.windows[60:])
        assert all(e.window_count <= 2 for e in rep.episodes)
    assert flagged <= 5 * 3
