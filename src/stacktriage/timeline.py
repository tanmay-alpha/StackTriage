"""Time-window aggregation of events. Every number comes from recorded events."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from .models import Level, LogEvent

WINDOW_CHOICES = (1, 5, 10, 30, 60, 300, 900, 1800, 3600, 7200, 21600, 86400)
TARGET_BUCKETS = 150
MAX_BUCKETS = 5000


def choose_window(span_seconds: float) -> int:
    """Smallest standard window that keeps the chart at <= TARGET_BUCKETS bars."""
    for w in WINDOW_CHOICES:
        if span_seconds / w <= TARGET_BUCKETS:
            return w
    return WINDOW_CHOICES[-1]


@dataclass(slots=True)
class Bucket:
    start: datetime
    window_seconds: int
    total: int = 0
    debug: int = 0
    info: int = 0
    warning: int = 0
    error: int = 0
    critical: int = 0
    unknown: int = 0
    distinct_groups: int = 0  # distinct error fingerprints active in this window
    new_groups: int = 0  # fingerprints whose first occurrence falls in this window

    @property
    def errors(self) -> int:
        """ERROR + CRITICAL events."""
        return self.error + self.critical

    @property
    def event_rate(self) -> float:
        """Events per second over the window."""
        return self.total / self.window_seconds

    @property
    def error_ratio(self) -> float:
        return self.errors / self.total if self.total else 0.0


@dataclass(slots=True)
class Timeline:
    window_seconds: int
    start: datetime | None
    end: datetime | None
    buckets: list[Bucket] = field(default_factory=list)
    untimed_events: int = 0


_LEVEL_IDX = {Level.DEBUG: 1, Level.INFO: 2, Level.WARNING: 3, Level.ERROR: 4, Level.CRITICAL: 5}


class TimelineBuilder:
    """Accumulates per-second counters (bounded by distinct seconds observed), then
    re-bins to any window size. Zero-filled windows mean "no events were recorded"."""

    def __init__(self) -> None:
        # sec -> [total, debug, info, warning, error, critical, unknown]
        self._sec: dict[int, list[int]] = {}
        self.untimed = 0

    def add(self, ev: LogEvent) -> None:
        if ev.timestamp is None:
            self.untimed += 1
            return
        sec = int(ev.timestamp.timestamp())
        row = self._sec.get(sec)
        if row is None:
            row = self._sec[sec] = [0] * 7
        row[0] += 1
        row[_LEVEL_IDX.get(ev.level, 6)] += 1

    @property
    def bounds(self) -> tuple[int, int] | None:
        if not self._sec:
            return None
        return min(self._sec), max(self._sec)

    def build(
        self, window_seconds: int | None, group_series: dict[str, dict[int, int]] | None = None,
    ) -> Timeline:
        b = self.bounds
        if b is None:
            return Timeline(window_seconds or 60, None, None, [], self.untimed)
        lo, hi = b
        w = window_seconds or choose_window(hi - lo)
        if w < 1 or (hi - lo) // w + 1 > MAX_BUCKETS:
            raise ValueError(f"window of {w}s would create more than {MAX_BUCKETS} buckets")
        first = lo - lo % w
        n = (hi - first) // w + 1
        buckets = [Bucket(datetime.fromtimestamp(first + i * w, tz=UTC), w) for i in range(n)]
        for sec, row in self._sec.items():
            bk = buckets[(sec - first) // w]
            bk.total += row[0]
            bk.debug += row[1]
            bk.info += row[2]
            bk.warning += row[3]
            bk.error += row[4]
            bk.critical += row[5]
            bk.unknown += row[6]
        for series in (group_series or {}).values():
            idx = {(s - first) // w for s in series}
            for i in idx:
                buckets[i].distinct_groups += 1
            buckets[(min(series) - first) // w].new_groups += 1
        return Timeline(w, datetime.fromtimestamp(lo, tz=UTC), datetime.fromtimestamp(hi, tz=UTC),
                        buckets, self.untimed)
