"""Streaming accumulation of events into error groups."""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import UTC, datetime

from .fingerprint import fingerprint, is_groupable
from .models import Level, LogEvent

MAX_SAMPLE_EVENT_IDS = 20
OVERFLOW_KEY = "overflow"


@dataclass(slots=True)
class ErrorGroup:
    fingerprint: str
    title: str
    service: str
    exc_type: str | None
    kind: str
    frame: str | None
    max_level: Level
    count: int = 0
    untimed_count: int = 0
    first_seen: datetime | None = None
    last_seen: datetime | None = None
    sample_event_id: str = ""
    sample_line_no: int = 0
    sample_message: str = ""
    sample_stack_trace: str | None = None
    event_ids: list[str] = field(default_factory=list)  # first MAX_SAMPLE_EVENT_IDS
    level_counts: dict[str, int] = field(default_factory=dict)
    # epoch-second -> occurrences; feeds the timeline and recurrence analysis
    series: dict[int, int] = field(default_factory=dict)


class Grouper:
    """Bounded-memory grouping. Memory is O(groups + distinct seconds per group),
    never O(total message text): only one representative event is kept per group."""

    def __init__(self, max_groups: int = 10_000) -> None:
        self.max_groups = max_groups
        self.groups: dict[str, ErrorGroup] = {}
        self.overflowed_events = 0

    def add(self, ev: LogEvent) -> ErrorGroup | None:
        if not is_groupable(ev):
            return None
        fp = fingerprint(ev)
        key = fp.key
        g = self.groups.get(key)
        if g is None:
            if len(self.groups) >= self.max_groups:
                self.overflowed_events += 1
                key = OVERFLOW_KEY
                g = self.groups.get(key)
                if g is None:
                    g = self.groups[key] = ErrorGroup(
                        OVERFLOW_KEY, "(additional error types beyond the group limit)",
                        "*", None, "overflow", None, ev.level,
                        sample_event_id=ev.event_id, sample_line_no=ev.line_no,
                        sample_message=ev.message, sample_stack_trace=ev.stack_trace,
                    )
            else:
                g = self.groups[key] = ErrorGroup(
                    key, fp.title, ev.service or "unknown", fp.exc_type, fp.kind, fp.frame,
                    ev.level, sample_event_id=ev.event_id, sample_line_no=ev.line_no,
                    sample_message=ev.message, sample_stack_trace=ev.stack_trace,
                )
        g.count += 1
        if ev.level > g.max_level:
            g.max_level = ev.level
        name = ev.level.name
        g.level_counts[name] = g.level_counts.get(name, 0) + 1
        if len(g.event_ids) < MAX_SAMPLE_EVENT_IDS:
            g.event_ids.append(ev.event_id)
        if ev.timestamp is None:
            g.untimed_count += 1
        else:
            if g.first_seen is None or ev.timestamp < g.first_seen:
                g.first_seen = ev.timestamp
            if g.last_seen is None or ev.timestamp > g.last_seen:
                g.last_seen = ev.timestamp
            sec = int(ev.timestamp.timestamp())
            g.series[sec] = g.series.get(sec, 0) + 1
        return g


def classify_recurrence(series: dict[int, int]) -> tuple[str, float | None]:
    """Describe how an error recurs, from the seconds in which it was seen.

    Returns (pattern, period_seconds):
      single    seen in <3 distinct seconds
      periodic  >=6 occurrence-seconds with near-constant gaps (CV < 0.25, median >= 2s)
      bursty    gap distribution dominated by short gaps plus long pauses (CV > 1.5)
      irregular anything else
    """
    secs = sorted(series)
    if len(secs) < 3:
        return "single", None
    gaps = [b - a for a, b in zip(secs, secs[1:])]
    mean = statistics.fmean(gaps)
    if mean == 0:
        return "single", None
    cv = statistics.pstdev(gaps) / mean
    med = statistics.median(gaps)
    if len(secs) >= 6 and cv < 0.25 and med >= 2:
        return "periodic", float(med)
    if cv > 1.5:
        return "bursty", None
    return "irregular", None


def utc(sec: int) -> datetime:
    return datetime.fromtimestamp(sec, tz=UTC)
