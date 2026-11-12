"""Compare the error groups of two imports (baseline = before a change, target = after).

Pure functions over already-persisted aggregates; no database access and no raw log text.

Matching is exact on the stored fingerprint. Nothing is merged fuzzily, so normalization
limits of the fingerprint (unquoted variable words, shifted frame line numbers) show up as a
"disappeared" + "new" pair rather than as one recurring group.

Rates
-----
* ``events`` basis (default): occurrences per 1,000 parsed events of the same import. Always
  defined (an import has at least one event) and normalizes for traffic/log volume, so a longer
  or busier capture does not look like a higher error rate by itself.
* ``time`` basis: timestamped occurrences per minute of timestamp coverage
  (``last_event - first_event``). Only offered when both imports have valid coverage.

Change test
-----------
For a fingerprint with counts c_b, c_t and exposures e_b, e_t (events or minutes), under the null
hypothesis of equal rates c_t | (c_b + c_t) ~ Binomial(c_b + c_t, e_t / (e_b + e_t)). The exact
two-sided p-value is computed in log space; the total cost is bounded by the number of error
occurrences in both imports. p-values are adjusted across all compared groups with
Benjamini-Hochberg. A recurring group is ``increased``/``decreased`` only when the adjusted value is
below ``ALPHA`` *and* the rate ratio is at least ``MIN_FOLD`` (or at most 1 / ``MIN_FOLD``).
"""
from __future__ import annotations

import dataclasses
import math
from typing import Literal

Basis = Literal["events", "time"]
Category = Literal["new", "disappeared", "recurring"]
Change = Literal["increased", "decreased", "no_clear_change", "insufficient_data"]
Evidence = Literal["significant", "weak", "low_sample"]

ALPHA = 0.05            # false-discovery rate for Benjamini-Hochberg
MIN_FOLD = 1.5          # smallest rate ratio reported as a change
MIN_OCCURRENCES = 10    # below this many occurrences in both imports together: low sample
MIN_COVERAGE_S = 60     # shortest timestamp span usable for per-minute rates
MAX_UNTIMED_SHARE = 0.10
LOW_VOLUME_EVENTS = 100
DURATION_RATIO_WARN = 4.0
SERVICE_SUMMARY_CAP = 50  # imports store only the 50 most frequent service names


@dataclasses.dataclass(frozen=True, slots=True)
class ImportStats:
    id: int
    format: str
    event_count: int
    untimed_events: int
    first_ms: int | None
    last_ms: int | None
    services: dict[str, int]
    truncated: bool
    groups_capped: bool = False  # the group limit was reached; extra error types were pooled and are excluded

    @property
    def timed_events(self) -> int:
        return self.event_count - self.untimed_events

    @property
    def duration_s(self) -> float | None:
        if self.first_ms is None or self.last_ms is None:
            return None
        return (self.last_ms - self.first_ms) / 1000

    @property
    def time_valid(self) -> bool:
        """Per-minute rates need a real time span, at least two timestamped events and few untimed ones."""
        d = self.duration_s
        return (d is not None and d >= MIN_COVERAGE_S and self.timed_events >= 2
                and self.untimed_events <= MAX_UNTIMED_SHARE * self.event_count)


@dataclasses.dataclass(frozen=True, slots=True)
class GroupStats:
    fingerprint: str
    title: str
    service: str
    exc_type: str | None
    frame: str | None
    max_level: int
    count: int
    untimed_count: int
    first_ms: int | None
    last_ms: int | None


@dataclasses.dataclass(frozen=True, slots=True)
class SideRates:
    count: int
    timed_count: int
    per_1k_events: float
    per_minute: float | None
    first_ms: int | None
    last_ms: int | None
    max_level: int


@dataclasses.dataclass(slots=True)
class Item:
    fingerprint: str
    title: str
    service: str
    exc_type: str | None
    frame: str | None
    category: Category
    change: Change | None
    baseline: SideRates | None
    target: SideRates | None
    rate_ratio: float | None
    percent_change: float | None
    p_value: float | None
    q_value: float | None
    evidence: Evidence
    bases_disagree: bool
    log2_change: float  # signed log2 of the 0.5-smoothed rate ratio on the chosen basis (> 0: worse in target)


@dataclasses.dataclass(frozen=True, slots=True)
class Warning_:
    code: str
    message: str


@dataclasses.dataclass(slots=True)
class Comparison:
    basis: Basis
    time_comparable: bool
    warnings: list[Warning_]
    items: list[Item]

    def totals(self, resolved: set[str]) -> dict[str, int]:
        t = dict.fromkeys(("new", "disappeared", "recurring", "increased", "decreased", "no_clear_change",
                           "insufficient_data", "resolved_observed"), 0)
        for it in self.items:
            t[it.category] += 1
            if it.change:
                t[it.change] += 1
            if it.target is not None and it.fingerprint in resolved:
                t["resolved_observed"] += 1
        return t


class BasisUnavailable(ValueError):
    pass


# --------------------------------------------------------------------------- statistics
def binom_two_sided(x: int, n: int, p: float) -> float:
    """Exact two-sided binomial test p-value (minimum-likelihood method, as in R's binom.test)."""
    if n == 0:
        return 1.0
    if p <= 0.0:
        return 1.0 if x == 0 else 0.0
    if p >= 1.0:
        return 1.0 if x == n else 0.0
    lp, lq, lnf = math.log(p), math.log1p(-p), math.lgamma(n + 1)

    def logpmf(k: int) -> float:
        return lnf - math.lgamma(k + 1) - math.lgamma(n - k + 1) + k * lp + (n - k) * lq

    cutoff = logpmf(x) + 1e-7  # relative tolerance for ties, in log space
    total = 0.0
    for k in range(n + 1):
        lk = logpmf(k)
        if lk <= cutoff:
            total += math.exp(lk)
    return min(1.0, total)


def bh_adjust(pvalues: list[float]) -> list[float]:
    """Benjamini-Hochberg adjusted p-values (q-values), same order as the input."""
    m = len(pvalues)
    order = sorted(range(m), key=lambda i: pvalues[i])
    q = [0.0] * m
    running = 1.0
    for rank in range(m, 0, -1):
        i = order[rank - 1]
        running = min(running, pvalues[i] * m / rank)
        q[i] = running
    return q


# --------------------------------------------------------------------------- comparison
def _side(g: GroupStats, imp: ImportStats) -> SideRates:
    timed = g.count - g.untimed_count
    d = imp.duration_s
    return SideRates(
        count=g.count, timed_count=timed, per_1k_events=g.count * 1000 / imp.event_count,
        per_minute=timed / (d / 60) if imp.time_valid and d else None,
        first_ms=g.first_ms, last_ms=g.last_ms, max_level=g.max_level)


def _exposure(imp: ImportStats, basis: Basis) -> float:
    return imp.event_count if basis == "events" else (imp.duration_s or 0) / 60


def _basis_count(s: SideRates | None, basis: Basis) -> int:
    if s is None:
        return 0
    return s.count if basis == "events" else s.timed_count


def _direction(r0: float, r1: float) -> int:
    """+1 / -1 when the target rate is at least MIN_FOLD times higher / lower (a zero side counts as infinitely
    lower), 0 otherwise. Uses the rates, not the displayed ratio, which is undefined for a zero baseline."""
    if r0 == r1:
        return 0
    if r0 == 0:
        return 1
    if r1 == 0:
        return -1
    ratio = r1 / r0
    return 1 if ratio >= MIN_FOLD else -1 if ratio <= 1 / MIN_FOLD else 0


def warnings_for(b: ImportStats, t: ImportStats) -> list[Warning_]:
    w: list[Warning_] = []
    add = lambda code, msg: w.append(Warning_(code, msg))  # noqa: E731
    if b.format != t.format:
        add("format_mismatch", f"Baseline was parsed as {b.format} and target as {t.format}; fingerprints from "
            "different log formats rarely match.")
    bs, ts = set(b.services), set(t.services)
    if len(bs) >= SERVICE_SUMMARY_CAP or len(ts) >= SERVICE_SUMMARY_CAP:
        add("service_list_truncated", f"An import has at least {SERVICE_SUMMARY_CAP} services and only the most "
            "frequent are stored, so service names are not compared.")
    elif bs and ts and not bs & ts:
        add("no_shared_services", "The imports share no service names, so they probably come from different "
            "applications; fingerprints include the service.")
    elif bs != ts:
        add("service_mismatch", "Service names differ between the imports: "
            f"only in baseline {sorted(bs - ts)[:5]}, only in target {sorted(ts - bs)[:5]}.")
    if b.first_ms is not None and t.first_ms is not None and b.last_ms is not None:
        if t.first_ms < b.first_ms:
            add("target_before_baseline", "The target capture starts before the baseline. Swap them if the "
                "baseline is meant to be the earlier period.")
        elif t.first_ms < b.last_ms:
            add("captures_overlap", "The two captures overlap in time, so the same occurrences may be counted in both.")
    for name, imp in (("baseline", b), ("target", t)):
        if imp.untimed_events:
            add(f"{name}_missing_timestamps", f"{imp.untimed_events} {name} events have no timestamp; they count "
                "towards per-1,000-event rates but not per-minute rates.")
        if imp.truncated:
            add(f"{name}_truncated", f"The {name} import was truncated at a parsing limit; later events are missing.")
        if imp.groups_capped:
            add(f"{name}_groups_capped", f"The {name} reached the error-group limit; further error types were pooled "
                "into an overflow bucket that is excluded from this comparison.")
        if imp.event_count < LOW_VOLUME_EVENTS:
            add(f"{name}_low_volume", f"The {name} has only {imp.event_count} events; rates are unstable.")
    if not (b.time_valid and t.time_valid):
        add("time_basis_unavailable", "Per-minute rates need at least a minute of timestamp coverage, two "
            "timestamped events and at most 10% untimed events in both imports.")
    else:
        db, dt = b.duration_s or 0, t.duration_s or 0
        if max(db, dt) / max(min(db, dt), 1e-9) > DURATION_RATIO_WARN:
            add("duration_mismatch", f"Capture lengths differ substantially ({db / 60:.1f} vs {dt / 60:.1f} min). "
                "Rates assume occurrences are spread evenly over each capture.")
    return w


def _classify(b: ImportStats, t: ImportStats, bg: dict[str, GroupStats], tg: dict[str, GroupStats],
              basis: Basis) -> list[Item]:
    eb, et = _exposure(b, basis), _exposure(t, basis)
    p_null = et / (eb + et) if eb + et > 0 else 0.5
    items: list[Item] = []
    rates: dict[str, tuple[float, float]] = {}
    for fp in sorted(bg.keys() | tg.keys()):
        g0, g1 = bg.get(fp), tg.get(fp)
        ref = g1 or g0
        assert ref is not None
        s0 = _side(g0, b) if g0 else None
        s1 = _side(g1, t) if g1 else None
        c0, c1 = _basis_count(s0, basis), _basis_count(s1, basis)
        r0, r1 = (c0 / eb if eb else 0.0), (c1 / et if et else 0.0)
        rates[fp] = (r0, r1)
        ratio = r1 / r0 if s0 and s1 and r0 > 0 else None
        smoothed = ((c1 + 0.5) / et) / ((c0 + 0.5) / eb) if eb and et else 1.0
        items.append(Item(
            fingerprint=fp, title=ref.title, service=ref.service, exc_type=ref.exc_type, frame=ref.frame,
            category="recurring" if g0 and g1 else "new" if g1 else "disappeared", change=None,
            baseline=s0, target=s1, rate_ratio=ratio,
            percent_change=(ratio - 1) * 100 if ratio is not None else None,
            p_value=binom_two_sided(c1, c0 + c1, p_null) if c0 + c1 else None, q_value=None,
            evidence="low_sample", bases_disagree=False, log2_change=math.log2(smoothed)))

    tested = [it for it in items if it.p_value is not None]
    for it, q in zip(tested, bh_adjust([it.p_value for it in tested])):  # type: ignore[misc]
        it.q_value = q
    for it in items:
        n = _basis_count(it.baseline, basis) + _basis_count(it.target, basis)
        significant = it.q_value is not None and it.q_value < ALPHA
        it.evidence = "low_sample" if n < MIN_OCCURRENCES else "significant" if significant else "weak"
        if it.category == "recurring":
            d = _direction(*rates[it.fingerprint])
            it.change = ("insufficient_data" if n < MIN_OCCURRENCES
                         else "increased" if significant and d > 0
                         else "decreased" if significant and d < 0
                         else "no_clear_change")
    return items


def compare(b: ImportStats, t: ImportStats, base_groups: list[GroupStats], target_groups: list[GroupStats],
            basis: Basis = "events") -> Comparison:
    time_comparable = b.time_valid and t.time_valid
    if basis == "time" and not time_comparable:
        raise BasisUnavailable("per-minute comparison needs valid timestamp coverage in both imports")
    warnings = warnings_for(b, t)
    bg = {g.fingerprint: g for g in base_groups}
    tg = {g.fingerprint: g for g in target_groups}
    if bg and tg and not bg.keys() & tg.keys():
        warnings.append(Warning_("no_shared_fingerprints", "No error group appears in both imports. Check that "
                                 "they come from the same application, service name and log format."))
    items = _classify(b, t, bg, tg, basis)
    if time_comparable:
        # Full verdict on the other basis (its own test and BH adjustment); flag rows where an increase or
        # decrease on one basis is not the verdict on the other.
        other = {it.fingerprint: it.change for it in _classify(b, t, bg, tg, "time" if basis == "events" else "events")}
        directional = {"increased", "decreased"}
        for it in items:
            o = other[it.fingerprint]
            it.bases_disagree = it.change != o and (it.change in directional or o in directional)
    return Comparison(basis=basis, time_comparable=time_comparable, warnings=warnings, items=items)
