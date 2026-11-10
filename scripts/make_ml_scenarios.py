"""Seeded generator of labelled incident scenarios for window anomaly detection.

Every scenario is a stream of LogEvents (levels, error messages, numeric metrics) that goes
through the real pipeline (`stacktriage.analyze`) and yields a Timeline plus one boolean
label per window (True = the window lies inside a labelled incident).

Design rule: the incidents are NOT separable by ERROR volume (log level) alone. Background:
60-second windows, ~40 INFO + ~1 WARNING + ~1.6 ERROR lines each, a slowly varying load
factor, INFO heartbeats carrying cpu_pct / latency_ms / queue_depth, six known error types
and a rare brand-new background error type.

    name             group   what happens                                              ERROR-count rule
    healthy          main    nothing (control for false positives)                     -
    error_spike      main    ERROR volume x8 for 12-18 windows (the easy case)         detects
    traffic_drop     main    traffic (and errors with it) falls to 10 % ("silent")     blind
    latency_rise     main    latency_ms +40 %, queue_depth +50 %, levels unchanged     blind
    new_error_type   main    3 new types REPLACE ~40 % of existing errors (volume same) blind
    busy_healthy     main    TRAP: 2.2x traffic, extra lines are all INFO, no incident false-alarm-free
    natural_bursts   main    TRAP: short x5 error bursts in train AND test, no incident false alarms
    noisy_mixed      main    bursts + busy period + traffic drop + error spike + latency rise
    slow_ramp        stress  error volume ramps x1 -> x5 over 60 windows (labelled from the start)
    early_incident   stress  error spike at windows 15-35, i.e. inside the training period
    short_log        stress  only 36 windows, spike near the end
    tiny_log         stress  only 20 windows: too short to train (degrades to "no findings")
    incident_at_start stress error spike at windows 0-12, the very start of the log
    incident_fills_baseline stress  error spike over 58 % of the training period (median breaks down)

Everything is deterministic given (name, seed). Python's `random` is used (no numpy).

    python scripts/make_ml_scenarios.py [--seed N]    # prints a summary of every scenario
"""
from __future__ import annotations

import argparse
import math
import random
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from stacktriage import Level, LogEvent, analyze  # noqa: E402
from stacktriage.timeline import Timeline  # noqa: E402

WINDOW_SECONDS = 60
N_WINDOWS = 240
TRAIN_FRACTION = 0.5
BASE_TIME = datetime(2026, 3, 5, 0, 0, 0, tzinfo=UTC)

EVAL_SEEDS = tuple(range(10))  # reported results
DEV_SEEDS = tuple(range(1000, 1006))  # thresholds / constants were tuned on these only

_KNOWN_ERRORS = (
    ("connection timeout to payments-db after {n}ms", 0.30),
    ("failed to render template invoice.html: missing key order_total", 0.22),
    ("upstream 502 from inventory-service on GET /stock/{n}", 0.16),
    ("cache refresh retries exhausted for session store", 0.12),
    ("unable to parse webhook payload: unexpected token at offset {n}", 0.10),
    ("rate limit exceeded for client key", 0.10),
)
_BACKGROUND_NOVEL = (
    "certificate renewal reminder failed for tenant", "scheduler skipped job nightly-compaction",
    "geo lookup returned empty region", "pdf export worker restarted unexpectedly",
    "stale lock detected on reconcile queue", "locale bundle missing fallback strings",
)
_INCIDENT_NEW_TYPES = (
    "refund ledger rejected entry with negative balance",
    "tokenizer vocabulary checksum mismatch during warmup",
    "shard rebalance aborted: replica lag above limit",
    "feature flag payload failed schema validation",
)


@dataclass(slots=True)
class Incident:
    kind: str
    start: int  # first window index of the incident (inclusive)
    end: int  # one past the last window of the incident


@dataclass(slots=True)
class Scenario:
    name: str
    group: str  # "main" | "stress"
    description: str
    seed: int
    timeline: Timeline
    labels: list[bool]  # one per timeline window: inside a labelled incident
    incidents: list[Incident]
    eval_from: int  # first window index that counts for evaluation (start of the "later period")
    train_fraction: float = TRAIN_FRACTION
    notes: list[str] = field(default_factory=list)

    @property
    def n_windows(self) -> int:
        return len(self.timeline.buckets)


@dataclass(slots=True)
class _Plan:
    """Per-window knobs; incidents and traps are modifiers applied to ranges."""

    n: int
    traffic: list[float] = field(default_factory=list)  # multiplier on INFO volume
    err: list[float] = field(default_factory=list)  # multiplier on the ERROR rate
    latency: list[float] = field(default_factory=list)
    queue: list[float] = field(default_factory=list)
    extra_errors: dict[int, list[tuple[str, float]]] = field(default_factory=dict)  # window -> [(message, lambda)] ADDED
    # window -> [(message, share of existing events replaced, force >= 1 replacement)]; volume-neutral
    replace_errors: dict[int, list[tuple[str, float, bool]]] = field(default_factory=dict)
    labels: list[bool] = field(default_factory=list)
    incidents: list[Incident] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.traffic = [1.0] * self.n
        self.err = [1.0] * self.n
        self.latency = [1.0] * self.n
        self.queue = [1.0] * self.n
        self.labels = [False] * self.n

    def mark(self, kind: str, a: int, b: int) -> None:
        b = min(b, self.n)
        for i in range(a, b):
            self.labels[i] = True
        self.incidents.append(Incident(kind, a, b))


def _poisson(rng: random.Random, lam: float) -> int:
    if lam <= 0:
        return 0
    if lam > 30:
        return max(0, round(rng.gauss(lam, math.sqrt(lam))))
    limit, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= rng.random()
        if p <= limit:
            return k
        k += 1


# ---- modifiers (each returns nothing; incidents also set labels) ---------------------------

def _error_spike(plan: _Plan, rng: random.Random, a: int, b: int, mult: float = 8.0, new_type: bool | None = None) -> None:
    for i in range(a, min(b, plan.n)):
        plan.err[i] = mult
    if new_type is None:
        new_type = rng.random() < 0.5
    if new_type:
        msg = rng.choice(_INCIDENT_NEW_TYPES)
        for i in range(a, min(b, plan.n)):
            plan.extra_errors.setdefault(i, []).append((msg, 2.0))
    plan.mark("error_spike", a, b)


def _traffic_drop(plan: _Plan, a: int, b: int) -> None:
    for i in range(a, min(b, plan.n)):
        plan.traffic[i] = 0.1
        plan.err[i] = 0.1  # errors scale with traffic: nothing looks wrong at ERROR level
    plan.mark("traffic_drop", a, b)


def _latency_rise(plan: _Plan, a: int, b: int) -> None:
    for i in range(a, min(b, plan.n)):
        plan.latency[i] = 1.4
        plan.queue[i] = 1.5
    plan.mark("latency_rise", a, b)


def _new_error_types(plan: _Plan, rng: random.Random, a: int) -> None:
    """Three new error types appear at offsets 0, 2, 4 and each replaces ~40 % of that window's
    existing ERROR events (volume unchanged). A type is guaranteed to show up in its first window
    only when that window has at least one ERROR event."""
    kinds = list(_INCIDENT_NEW_TYPES)
    rng.shuffle(kinds)
    for k, off in enumerate((0, 2, 4)):
        for i in range(a + off, min(a + 6, plan.n)):
            plan.replace_errors.setdefault(i, []).append((kinds[k], 0.4, i == a + off))
    plan.mark("new_error_type", a, a + 6)


def _busy_healthy(plan: _Plan, a: int, b: int) -> None:
    for i in range(a, min(b, plan.n)):
        plan.traffic[i] = 2.2  # error volume untouched: only extra INFO lines. NOT labelled.


def _natural_bursts(plan: _Plan, rng: random.Random, prob: float = 0.03, mult: float = 5.0) -> None:
    i = 0
    while i < plan.n:
        if rng.random() < prob:
            length = rng.choice((1, 1, 2))
            for j in range(i, min(i + length, plan.n)):
                plan.err[j] = max(plan.err[j], mult)
            i += length + 2
        else:
            i += 1


# ---- event synthesis -----------------------------------------------------------------------

def _synthesize(plan: _Plan, rng: random.Random) -> list[LogEvent]:
    events: list[LogEvent] = []
    ar = 0.0
    novel_pool = list(_BACKGROUND_NOVEL)
    rng.shuffle(novel_pool)
    lat_sigma = 0.3
    line = 0
    known_msgs = [m for m, _ in _KNOWN_ERRORS]
    known_w = [w for _, w in _KNOWN_ERRORS]

    def mk(sec: int, level: Level, msg: str, metrics: dict[str, float] | None = None) -> LogEvent:
        nonlocal line
        line += 1
        return LogEvent(
            f"{line}", line, "python", BASE_TIME + timedelta(seconds=sec), level, "api", "app", msg,
            metrics=metrics or {},
        )

    for w in range(plan.n):
        ar = 0.7 * ar + math.sqrt(1 - 0.49) * rng.gauss(0, 1)
        load = 1.0 + 0.08 * ar
        base = w * WINDOW_SECONDS

        def at() -> int:
            return base + rng.randrange(WINDOW_SECONDS)

        for _ in range(_poisson(rng, 40 * load * plan.traffic[w])):
            if rng.random() < 0.5:
                metrics = {
                    "cpu_pct": max(0.0, rng.gauss(30, 6)),
                    "latency_ms": rng.lognormvariate(
                        math.log(80 * plan.latency[w]) - lat_sigma**2 / 2, lat_sigma),
                    "queue_depth": float(_poisson(rng, 3 * plan.queue[w])),
                }
                events.append(mk(at(), Level.INFO, "heartbeat ok", metrics))
            else:
                events.append(mk(at(), Level.INFO, "request handled"))
        for _ in range(_poisson(rng, 1.2 * load)):
            events.append(mk(at(), Level.WARNING, "slow response from downstream"))
        # Error events of this window: the count comes from the unchanged background process.
        # Novel types only *replace* messages of events that already exist, so the per-window
        # ERROR count distribution of a novelty scenario equals the healthy one.
        err_msgs = [rng.choices(known_msgs, known_w)[0].format(n=rng.randrange(100, 9999))
                    for _ in range(_poisson(rng, 1.6 * load * plan.err[w]))]
        if novel_pool and err_msgs and rng.random() < 0.004:
            err_msgs[rng.randrange(len(err_msgs))] = novel_pool.pop()
        replaced: set[int] = set()
        for msg, share, force in plan.replace_errors.get(w, ()):
            free = [i for i in range(len(err_msgs)) if i not in replaced]
            hit = [i for i in free if rng.random() < share]
            if force and free and not hit:
                hit = [rng.choice(free)]  # the type's first appearance (its novelty window)
            for i in hit:
                err_msgs[i] = msg
                replaced.add(i)
        for msg in err_msgs:
            events.append(mk(at(), Level.ERROR, msg))
        for msg, lam in plan.extra_errors.get(w, ()):
            for _ in range(_poisson(rng, lam)):
                events.append(mk(at(), Level.ERROR, msg))
    events.sort(key=lambda e: e.timestamp)
    return events


def _finish(name: str, group: str, description: str, seed: int, plan: _Plan, rng: random.Random,
            eval_from: int | None = None) -> Scenario:
    events = _synthesize(plan, rng)
    tl = analyze(events, window_seconds=WINDOW_SECONDS).timeline
    off = round((tl.buckets[0].start - BASE_TIME).total_seconds() / WINDOW_SECONDS)
    labels = [plan.labels[off + i] if 0 <= off + i < plan.n else False for i in range(len(tl.buckets))]
    incidents = [Incident(i.kind, i.start - off, i.end - off) for i in plan.incidents]
    n = len(tl.buckets)
    return Scenario(
        name, group, description, seed, tl, labels, incidents,
        eval_from=int(n * TRAIN_FRACTION) if eval_from is None else eval_from,
    )


# ---- scenario builders ---------------------------------------------------------------------

def _b_healthy(plan, rng):
    pass


def _b_error_spike(plan, rng):
    a = rng.randint(135, 175)
    _error_spike(plan, rng, a, a + rng.randint(12, 18))


def _b_traffic_drop(plan, rng):
    a = rng.randint(135, 175)
    _traffic_drop(plan, a, a + rng.randint(15, 25))


def _b_latency_rise(plan, rng):
    a = rng.randint(135, 170)
    _latency_rise(plan, a, a + rng.randint(18, 28))


def _b_new_error_type(plan, rng):
    _new_error_types(plan, rng, rng.randint(135, 200))


def _b_busy_healthy(plan, rng):
    a = rng.randint(135, 180)
    _busy_healthy(plan, a, a + 25)


def _b_natural_bursts(plan, rng):
    _natural_bursts(plan, rng)


def _b_noisy_mixed(plan, rng):
    _natural_bursts(plan, rng)
    a = rng.randint(124, 130)
    _busy_healthy(plan, a, a + 20)
    a = rng.randint(148, 154)
    _traffic_drop(plan, a, a + rng.randint(12, 18))
    a = rng.randint(172, 178)
    _error_spike(plan, rng, a, a + rng.randint(8, 12), mult=7.0)
    a = rng.randint(196, 202)
    _latency_rise(plan, a, a + rng.randint(18, 24))


def _b_slow_ramp(plan, rng):
    a, length = 140, 60
    for k in range(length):
        plan.err[a + k] = 1.0 + 4.0 * k / (length - 1)
    plan.mark("slow_ramp", a, a + length)


def _b_early_incident(plan, rng):
    _error_spike(plan, rng, 15, 35, new_type=True)


def _b_short_log(plan, rng):
    _error_spike(plan, rng, 26, 32, new_type=False)


def _b_tiny_log(plan, rng):
    _error_spike(plan, rng, 14, 18, new_type=False)


def _b_incident_at_start(plan, rng):
    _error_spike(plan, rng, 0, 12, new_type=False)


def _b_incident_fills_baseline(plan, rng):
    _error_spike(plan, rng, 0, 70, new_type=False)


@dataclass(frozen=True, slots=True)
class _Spec:
    group: str
    description: str
    build: Callable[[_Plan, random.Random], None]
    n_windows: int = N_WINDOWS
    eval_from_start: bool = False


SPECS: dict[str, _Spec] = {
    "healthy": _Spec("main", "no incident (false-positive control)", _b_healthy),
    "error_spike": _Spec("main", "ERROR volume x8 for 12-18 windows", _b_error_spike),
    "traffic_drop": _Spec("main", "traffic and errors fall to 10 % (silent failure)", _b_traffic_drop),
    "latency_rise": _Spec("main", "latency_ms +40 %, queue_depth +50 %, log levels unchanged", _b_latency_rise),
    "new_error_type": _Spec("main", "3 new error types replace ~40 % of existing ERROR events, volume unchanged",
                            _b_new_error_type),
    "busy_healthy": _Spec("main", "TRAP 2.2x traffic of INFO lines, no incident", _b_busy_healthy),
    "natural_bursts": _Spec("main", "TRAP short x5 ERROR bursts in train and test, no incident", _b_natural_bursts),
    "noisy_mixed": _Spec("main", "bursts + busy period + traffic drop + error spike + latency rise", _b_noisy_mixed),
    "slow_ramp": _Spec("stress", "ERROR volume ramps x1 -> x5 over 60 windows", _b_slow_ramp),
    "early_incident": _Spec("stress", "error spike inside the training period (windows 15-35)",
                            _b_early_incident, eval_from_start=True),
    "short_log": _Spec("stress", "36 windows only, spike at windows 26-32", _b_short_log, n_windows=36),
    "tiny_log": _Spec("stress", "20 windows only (10 would be the baseline), spike at windows 14-18",
                      _b_tiny_log, n_windows=20),
    "incident_at_start": _Spec("stress", "error spike at windows 0-12, the very start of the log",
                               _b_incident_at_start, eval_from_start=True),
    "incident_fills_baseline": _Spec("stress", "error spike at windows 0-70 = 58 % of the training period",
                                     _b_incident_fills_baseline, eval_from_start=True),
}
SCENARIO_NAMES = tuple(SPECS)
TRAIN_PERIOD_NAMES = tuple(n for n, s in SPECS.items() if s.eval_from_start)  # incident inside the training period
MAIN_NAMES = tuple(n for n, s in SPECS.items() if s.group == "main")
STRESS_NAMES = tuple(n for n, s in SPECS.items() if s.group == "stress")


def make_scenario(name: str, seed: int) -> Scenario:
    spec = SPECS[name]
    rng = random.Random(f"stacktriage-ml:{name}:{seed}")
    plan = _Plan(spec.n_windows)
    spec.build(plan, rng)
    return _finish(name, spec.group, spec.description, seed, plan, rng, 0 if spec.eval_from_start else None)


def make_all(seed: int, names: tuple[str, ...] = SCENARIO_NAMES) -> list[Scenario]:
    return [make_scenario(n, seed) for n in names]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()
    print(f"{'scenario':<16}{'group':<8}{'windows':>8}{'incident windows':>18}  incidents")
    for sc in make_all(args.seed):
        inc = ", ".join(f"{i.kind}[{i.start},{i.end})" for i in sc.incidents) or "-"
        print(f"{sc.name:<16}{sc.group:<8}{sc.n_windows:>8}{sum(sc.labels):>18}  {inc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
