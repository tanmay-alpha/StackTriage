"""Per-bucket numeric metric means in the timeline (feature input of the anomaly detector)."""
import io
import math
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from stacktriage import Level, LogEvent, analyze, parse

T0 = datetime(2026, 3, 5, 10, 0, 0, tzinfo=UTC)
FIXTURES = Path(__file__).parent / "fixtures"


def ev(sec, metrics=None, level=Level.INFO, msg="ok"):
    return LogEvent(f"{sec}{msg}{metrics}", 1, "csv", T0 + timedelta(seconds=sec), level, "svc", None, msg,
                    metrics=dict(metrics or {}))


def test_metric_mean_per_bucket_and_absent_when_not_carried():
    events = [
        ev(1, {"latency_ms": 100.0, "cpu_pct": 10.0}),
        ev(2, {"latency_ms": 200.0}),
        ev(3),  # carries nothing: does not dilute the mean
        ev(65, {"cpu_pct": 40.0}),
        ev(130),
    ]
    tl = analyze(events, window_seconds=60).timeline
    assert [b.total for b in tl.buckets] == [3, 1, 1]
    assert tl.buckets[0].metrics == {"cpu_pct": 10.0, "latency_ms": 150.0}
    assert tl.buckets[1].metrics == {"cpu_pct": 40.0}  # latency_ms absent, not 0.0
    assert tl.buckets[2].metrics == {}


def test_metric_mean_is_independent_of_window_size_choice():
    events = [ev(s, {"q": float(s)}) for s in range(0, 120)]
    small = analyze(events, window_seconds=60).timeline
    assert small.buckets[0].metrics["q"] == pytest.approx(sum(range(60)) / 60)
    assert small.buckets[1].metrics["q"] == pytest.approx(sum(range(60, 120)) / 60)
    big = analyze(events, window_seconds=300).timeline
    assert big.buckets[0].metrics["q"] == pytest.approx(sum(range(120)) / 120)


def test_non_finite_metric_values_are_ignored():
    tl = analyze([ev(1, {"m": math.nan}), ev(2, {"m": math.inf}), ev(3, {"m": 4.0})], window_seconds=60).timeline
    assert tl.buckets[0].metrics == {"m": 4.0}


def test_metric_mean_of_huge_finite_values_does_not_overflow():
    big = 1.7e308
    # same second (first aggregation stage), different seconds (second stage), opposite signs
    same_sec = analyze([ev(1, {"m": big}), ev(1, {"m": big}), ev(1, {"m": big})], window_seconds=60).timeline
    across = analyze([ev(1, {"m": big}), ev(2, {"m": big}), ev(30, {"m": big})], window_seconds=60).timeline
    opposite = analyze([ev(1, {"m": big}), ev(1, {"m": -big}), ev(2, {"m": big}), ev(3, {"m": -big})],
                       window_seconds=60).timeline
    assert same_sec.buckets[0].metrics["m"] == pytest.approx(big)
    assert across.buckets[0].metrics["m"] == pytest.approx(big)
    assert abs(opposite.buckets[0].metrics["m"]) < big * 1e-9
    for tl in (same_sec, across, opposite):
        assert all(math.isfinite(v) for b in tl.buckets for v in b.metrics.values())


def test_metric_means_on_real_telemetry_fixture():
    sess = parse(io.BytesIO((FIXTURES / "telemetry.csv").read_bytes()))
    events = list(sess)
    res = analyze(events, window_seconds=600)
    carried = [b for b in res.timeline.buckets if b.metrics]
    assert carried, "telemetry.csv carries cpu_pct / latency_ms / queue_depth"
    for b in carried:
        assert set(b.metrics) <= {"cpu_pct", "latency_ms", "queue_depth"}
    # recompute one window's latency mean directly from the events
    b = carried[0]
    lo = int(b.start.timestamp())
    vals = [e.metrics["latency_ms"] for e in events
            if e.timestamp is not None and lo <= int(e.timestamp.timestamp()) < lo + 600
            and "latency_ms" in e.metrics]
    assert vals and abs(b.metrics["latency_ms"] - sum(vals) / len(vals)) < 1e-9
