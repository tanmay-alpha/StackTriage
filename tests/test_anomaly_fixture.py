"""The real telemetry fixture: a healthy hour of heartbeats, then 'timeout calling inventory' errors."""
import io
from datetime import UTC, datetime
from pathlib import Path

import pytest

from stacktriage import analyze, parse
from stacktriage.anomaly import detect_anomalies

FIXTURE = Path(__file__).parent / "fixtures" / "telemetry.csv"
ONSET = datetime(2026, 3, 5, 11, 0, 0, tzinfo=UTC)  # first upstream_timeout in expected/telemetry.json


@pytest.fixture(scope="module")
def timeline():
    events = list(parse(io.BytesIO(FIXTURE.read_bytes())))
    return analyze(events, window_seconds=60).timeline


@pytest.mark.parametrize("method", ("statistical", "isolation_forest"))
def test_incident_onset_is_found_after_a_healthy_training_hour(timeline, method):
    rep = detect_anomalies(timeline, method=method, seed=0)
    assert len(rep.windows) == 120
    assert rep.train_range == (timeline.buckets[0].start, ONSET)
    before = [w for w in rep.windows if w.start < ONSET]
    after = [w for w in rep.windows if w.start >= ONSET]
    # the IsolationForest threshold is calibrated at the 90th percentile of training scores, so it
    # is expected to flag a few healthy windows (see docs/ml-evaluation.md)
    assert sum(w.is_anomalous for w in before) <= (1 if method == "statistical" else 8)
    assert sum(w.is_anomalous for w in after) >= 0.9 * len(after)
    first = next(w for w in rep.windows if w.is_anomalous and w.start >= ONSET)
    assert (first.start - ONSET).total_seconds() <= 60
    assert rep.episodes and rep.episodes[-1].end == rep.windows[-1].end  # still ongoing at end of data
    assert any("still ongoing" in x for x in rep.episodes[-1].evidence)


def test_metric_means_become_features_on_real_telemetry(timeline):
    rep = detect_anomalies(timeline, method="statistical")
    feats = set(rep.parameters["features"])
    assert {"metric.cpu_pct", "metric.latency_ms", "metric.queue_depth"} <= feats
    ongoing = rep.episodes[-1]
    assert ongoing.classification in ("sustained_shift", "spike")
    assert "error_count" in ongoing.features or "error_ratio" in ongoing.features
    assert ongoing.severity in ("high", "critical")  # 70 % error lines for an hour, ongoing
