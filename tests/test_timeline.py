from datetime import UTC, datetime, timedelta

from stacktriage import Level, LogEvent, analyze
from stacktriage.grouping import classify_recurrence
from stacktriage.timeline import choose_window

T0 = datetime(2026, 3, 5, 10, 0, 0, tzinfo=UTC)


def ev(sec, msg="boom", level=Level.ERROR, ts=True):
    return LogEvent(str(sec) + msg, 1, "python", T0 + timedelta(seconds=sec) if ts else None, level, "svc", None, msg)


def test_counts_come_from_events_and_gaps_are_zero():
    events = [ev(0, "ok", Level.INFO), ev(5, "e"), ev(6, "e"), ev(125, "ok", Level.INFO), ev(130, "e")]
    res = analyze(events, window_seconds=60)
    tl = res.timeline
    assert [b.total for b in tl.buckets] == [3, 0, 2]
    assert [b.errors for b in tl.buckets] == [2, 0, 1]
    assert tl.buckets[1].total == 0 and tl.buckets[1].error_ratio == 0.0
    assert sum(b.total for b in tl.buckets) == res.total_events == 5
    assert tl.buckets[0].event_rate == 3 / 60
    assert tl.buckets[0].new_groups == 1 and tl.buckets[2].new_groups == 0
    assert tl.buckets[0].start == T0


def test_buckets_align_to_window_multiples():
    res = analyze([ev(70), ev(130)], window_seconds=60)
    assert all(int(b.start.timestamp()) % 60 == 0 for b in res.timeline.buckets)


def test_untimed_events_excluded_from_timeline_but_grouped():
    res = analyze([ev(1), ev(0, ts=False), ev(0, ts=False)], window_seconds=60)
    assert res.timeline.untimed_events == 2 and sum(b.total for b in res.timeline.buckets) == 1
    g = res.groups[0]
    assert g.count == 3 and g.untimed_count == 2 and any("no timestamp" in n for n in res.notes)


def test_empty_and_untimed_only():
    assert analyze([]).timeline.buckets == []
    assert analyze([ev(0, ts=False)]).timeline.buckets == []


def test_auto_window_bounds_bucket_count():
    for span in (1, 59, 3600, 86400, 30 * 86400):
        w = choose_window(span)
        assert span / w <= 150 or w == 86400
    events = [ev(0), ev(2 * 86400)]
    assert 1 <= len(analyze(events).timeline.buckets) <= 200


def test_invalid_window_rejected():
    import pytest

    with pytest.raises(ValueError):
        analyze([ev(0), ev(10**7)], window_seconds=1)


def test_group_activity_ongoing_vs_ended():
    events = [ev(s, "still") for s in range(0, 1000, 10)] + [ev(s, "gone") for s in range(0, 100, 10)]
    events += [ev(1000, "ok", Level.INFO)]
    res = analyze(events, window_seconds=60)
    act = {g.title: res.activity[g.fingerprint] for g in res.groups}
    assert act["still"].status == "ongoing" and act["gone"].status == "ended"
    assert act["still"].recurrence == "periodic" and act["still"].period_seconds == 10


def test_first_last_seen_and_peak_window():
    events = [ev(10), ev(130), ev(131), ev(132), ev(400)]
    res = analyze(events, window_seconds=60)
    g = res.groups[0]
    assert g.first_seen == T0 + timedelta(seconds=10) and g.last_seen == T0 + timedelta(seconds=400)
    a = res.activity[g.fingerprint]
    assert a.peak_window_count == 3 and a.peak_window_start == T0 + timedelta(seconds=120) and a.active_windows == 3


def test_recurrence_classes():
    assert classify_recurrence({0: 1, 1: 1})[0] == "single"
    assert classify_recurrence({t: 1 for t in range(0, 600, 60)}) == ("periodic", 60.0)
    assert classify_recurrence({t: 1 for t in [0, 1, 2, 3, 4, 5, 3000, 3001, 3002]})[0] == "bursty"
    assert classify_recurrence({t: 1 for t in [0, 7, 9, 30, 31, 77, 120]})[0] in ("irregular", "bursty")


def test_info_events_are_not_grouped():
    res = analyze([ev(0, "hello", Level.INFO)] * 5)
    assert res.groups == [] and res.total_events == 5


def test_group_limit_overflow_is_visible():
    events = [ev(i, f"unique message number {'x' * i}") for i in range(10)]
    res = analyze(events, max_groups=3)
    assert len(res.groups) == 4 and res.overflowed_events == 7
    assert sum(g.count for g in res.groups) == 10 and any("overflow" in n for n in res.notes)
