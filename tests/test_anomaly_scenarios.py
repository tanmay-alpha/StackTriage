"""The labelled scenarios must not be solvable by log level alone, and must be reproducible."""
from __future__ import annotations

import statistics

import pytest
from make_ml_scenarios import MAIN_NAMES, SCENARIO_NAMES, make_scenario


def _error_rule(sc):
    counts = [b.errors for b in sc.timeline.buckets]
    train = counts[: sc.eval_from]
    thr = statistics.fmean(train) + 3 * statistics.pstdev(train)
    return [c > thr for c in counts[sc.eval_from:]]


def test_generator_is_deterministic_and_seed_sensitive():
    for name in SCENARIO_NAMES:
        a, b, c = make_scenario(name, 4), make_scenario(name, 4), make_scenario(name, 5)
        sig = lambda s: [(x.total, x.errors, x.new_groups, tuple(x.metrics.items())) for x in s.timeline.buckets]  # noqa: E731
        assert sig(a) == sig(b) and a.labels == b.labels and a.incidents == b.incidents
        assert sig(a) != sig(c)


def test_labels_align_with_timeline_and_incidents():
    for name in SCENARIO_NAMES:
        sc = make_scenario(name, 0)
        assert len(sc.labels) == len(sc.timeline.buckets)
        assert sum(sc.labels) == sum(i.end - i.start for i in sc.incidents)
        assert all(all(sc.labels[i.start:i.end]) for i in sc.incidents)
    for name in ("healthy", "busy_healthy", "natural_bursts"):
        assert not any(make_scenario(name, 0).labels)  # traps and control carry no incident label


def test_main_incidents_are_in_the_later_period_only():
    for name in MAIN_NAMES:
        sc = make_scenario(name, 1)
        assert all(i.start >= sc.eval_from for i in sc.incidents)
    early = make_scenario("early_incident", 1)
    assert early.incidents[0].end <= early.n_windows // 2 and early.eval_from == 0


@pytest.mark.parametrize("name", ("traffic_drop", "latency_rise", "new_error_type"))
def test_error_level_alone_does_not_show_these_incidents(name):
    for seed in range(4):
        sc = make_scenario(name, seed)
        errors = [b.errors for b in sc.timeline.buckets]
        inc = [e for e, lab in zip(errors, sc.labels) if lab]
        train = errors[: sc.eval_from]
        assert max(inc) <= max(train) + 3, "incident windows must sit within the normal ERROR range"
        flagged = _error_rule(sc)[sc.incidents[0].start - sc.eval_from: sc.incidents[0].end - sc.eval_from]
        assert sum(flagged) / len(flagged) < 0.5


def test_traffic_drop_error_count_is_below_normal():
    sc = make_scenario("traffic_drop", 0)
    inc = [b.errors for b, lab in zip(sc.timeline.buckets, sc.labels) if lab]
    base = [b.errors for b in sc.timeline.buckets[: sc.eval_from]]
    assert statistics.fmean(inc) < statistics.fmean(base) / 2


def test_busy_healthy_adds_info_volume_not_errors():
    sc = make_scenario("busy_healthy", 0)
    b = sc.timeline.buckets
    totals = [x.total for x in b]
    base = statistics.fmean(totals[: sc.eval_from])
    busy = [i for i in range(sc.eval_from, len(b)) if totals[i] > 1.8 * base]
    assert len(busy) >= 15
    assert statistics.fmean(b[i].errors for i in busy) < 3 * statistics.fmean(x.errors for x in b[: sc.eval_from])


def test_natural_bursts_exist_in_train_and_test():
    sc = make_scenario("natural_bursts", 2)
    errors = [b.errors for b in sc.timeline.buckets]
    base = statistics.fmean(errors[: sc.eval_from])
    assert any(e > 2.5 * base + 3 for e in errors[: sc.eval_from])
    assert any(e > 2.5 * base + 3 for e in errors[sc.eval_from:])


def test_single_feature_error_rule_is_not_perfect_on_the_main_scenarios():
    for seed in range(3):
        labels, flags = [], []
        for name in MAIN_NAMES:
            sc = make_scenario(name, seed)
            labels += sc.labels[sc.eval_from:]
            flags += _error_rule(sc)
        tp = sum(1 for a, b in zip(labels, flags) if a and b)
        fp = sum(1 for a, b in zip(labels, flags) if not a and b)
        fn = sum(1 for a, b in zip(labels, flags) if a and not b)
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn)
        assert recall < 0.5 and precision < 1.0
        assert 2 * precision * recall / max(precision + recall, 1e-9) < 0.8


def test_metrics_are_carried_by_events_through_the_real_pipeline():
    sc = make_scenario("latency_rise", 0)
    pre = [b.metrics["latency_ms"] for b in sc.timeline.buckets[: sc.eval_from]]
    inc = [b.metrics["latency_ms"] for b, lab in zip(sc.timeline.buckets, sc.labels) if lab]
    assert statistics.fmean(inc) > 1.25 * statistics.fmean(pre)
