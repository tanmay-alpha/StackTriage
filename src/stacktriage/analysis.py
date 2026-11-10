"""Single-pass analysis: events -> error groups + timeline + per-group activity."""
from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime

from .grouping import ErrorGroup, Grouper, classify_recurrence, utc
from .models import LogEvent
from .timeline import Timeline, TimelineBuilder


@dataclass(slots=True)
class GroupActivity:
    fingerprint: str
    active_windows: int
    peak_window_start: datetime | None
    peak_window_count: int
    rate_per_minute: float | None  # occurrences / minute over first..last seen (min 1 minute)
    recurrence: str  # single | periodic | bursty | irregular
    period_seconds: float | None
    status: str  # ongoing | ended | unknown (relative to the end of the log)


@dataclass(slots=True)
class Analysis:
    total_events: int
    level_counts: dict[str, int]
    service_counts: dict[str, int]
    first_event: datetime | None
    last_event: datetime | None
    groups: list[ErrorGroup]  # sorted: count desc, then fingerprint
    activity: dict[str, GroupActivity]
    timeline: Timeline
    grouped_events: int
    overflowed_events: int = 0
    notes: list[str] = field(default_factory=list)


def analyze(events: Iterable[LogEvent], window_seconds: int | None = None, max_groups: int = 10_000) -> Analysis:
    grouper = Grouper(max_groups=max_groups)
    tl = TimelineBuilder()
    levels: Counter[str] = Counter()
    services: Counter[str] = Counter()
    total = 0
    for ev in events:
        total += 1
        levels[ev.level.name] += 1
        services[ev.service or "unknown"] += 1
        tl.add(ev)
        grouper.add(ev)

    groups = sorted(grouper.groups.values(), key=lambda g: (-g.count, g.fingerprint))
    timeline = tl.build(window_seconds, {g.fingerprint: g.series for g in groups if g.series})
    bounds = tl.bounds
    span = (bounds[1] - bounds[0]) if bounds else 0
    w = timeline.window_seconds
    recent_cutoff = (bounds[1] - max(2 * w, int(span * 0.05))) if bounds else 0

    activity: dict[str, GroupActivity] = {}
    for g in groups:
        if not g.series:
            activity[g.fingerprint] = GroupActivity(g.fingerprint, 0, None, 0, None, "single", None, "unknown")
            continue
        per_window: Counter[int] = Counter()
        for sec, n in g.series.items():
            per_window[sec - sec % w] += n
        peak_start, peak_n = max(per_window.items(), key=lambda kv: (kv[1], -kv[0]))
        g_span = max(max(g.series) - min(g.series), 60)
        pattern, period = classify_recurrence(g.series)
        timed = g.count - g.untimed_count
        status = "ongoing" if max(g.series) >= recent_cutoff and span > 0 else "ended"
        if span == 0:
            status = "unknown"
        activity[g.fingerprint] = GroupActivity(
            g.fingerprint, len(per_window), utc(peak_start), peak_n,
            timed / (g_span / 60), pattern, period, status,
        )

    notes: list[str] = []
    if tl.untimed:
        notes.append(f"{tl.untimed} events have no timestamp and are excluded from the timeline.")
    if grouper.overflowed_events:
        notes.append(f"{grouper.overflowed_events} events fell into the overflow group (group limit reached).")
    return Analysis(
        total_events=total, level_counts=dict(levels), service_counts=dict(services),
        first_event=timeline.start, last_event=timeline.end, groups=groups, activity=activity,
        timeline=timeline, grouped_events=sum(g.count for g in groups),
        overflowed_events=grouper.overflowed_events, notes=notes,
    )
