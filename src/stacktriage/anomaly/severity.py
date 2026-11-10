"""Operational incident severity from the evidence of an episode.

Deliberately has no access to the anomaly score: two episodes with the same score can have
different severity and a higher score can mean a lower severity. Each rule below adds
points; the total maps to a level. Thresholds are absolute operational quantities (error
volume above the baseline, share of error lines, wall-clock duration, relative traffic loss,
relative metric shift, number of new error groups), not statistical ones.

| evidence                                         | points |
|--------------------------------------------------|--------|
| peak error ratio >= 50 % / >= 20 % / >= 10 %     | 3 / 2 / 1 |
| errors above baseline >= 200 / >= 50 / >= 10     | 3 / 2 / 1 |
| duration >= 30 min / >= 5 min                    | 2 / 1 |
| still ongoing at the end of the data             | 1 |
| new error groups >= 3 / >= 1                     | 2 / 1 |
| event rate lost vs baseline >= 80 % / 50 % / 25 %| 3 / 2 / 1 |
| largest numeric-metric shift >= 100 % / >= 30 %  | 2 / 1 |

Level: 0-1 low, 2-3 medium, 4-6 high, >= 7 critical.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class EpisodeEvidence:
    window_count: int
    duration_seconds: float
    ongoing: bool  # episode includes the last window of the data
    excess_errors: float  # sum over windows of max(0, errors - baseline errors per window)
    peak_error_ratio: float
    new_groups: int
    rate_drop: float  # 0..1 fraction of baseline event rate lost (mean over the episode)
    metric_shift: float  # largest |episode mean / baseline - 1| over numeric metrics
    metric_shift_name: str | None = None


@dataclass(frozen=True, slots=True)
class Grade:
    level: str
    points: int
    reasons: list[str] = field(default_factory=list)


def _tier(value: float, tiers: tuple[tuple[float, int], ...]) -> int:
    for threshold, pts in tiers:
        if value >= threshold:
            return pts
    return 0


def grade(ev: EpisodeEvidence) -> Grade:
    reasons: list[str] = []
    total = 0

    def add(pts: int, text: str) -> None:
        nonlocal total
        if pts:
            total += pts
            reasons.append(f"{text} (+{pts})")

    add(_tier(ev.peak_error_ratio, ((0.5, 3), (0.2, 2), (0.1, 1))),
        f"peak error ratio {ev.peak_error_ratio:.1%}")
    add(_tier(ev.excess_errors, ((200, 3), (50, 2), (10, 1))),
        f"{ev.excess_errors:.0f} errors above baseline")
    add(_tier(ev.duration_seconds, ((1800, 2), (300, 1))),
        f"duration {ev.duration_seconds / 60:.0f} min over {ev.window_count} window(s)")
    if ev.ongoing:
        add(1, "still ongoing at the end of the data")
    add(_tier(ev.new_groups, ((3, 2), (1, 1))), f"{ev.new_groups} new error group(s)")
    add(_tier(ev.rate_drop, ((0.8, 3), (0.5, 2), (0.25, 1))),
        f"event rate {ev.rate_drop:.0%} below baseline")
    add(_tier(ev.metric_shift, ((1.0, 2), (0.3, 1))),
        f"metric {ev.metric_shift_name} shifted {ev.metric_shift:.0%}")
    if not reasons:
        reasons.append("no operational evidence beyond statistical unusualness")
    level = "critical" if total >= 7 else "high" if total >= 4 else "medium" if total >= 2 else "low"
    return Grade(level, total, reasons)
