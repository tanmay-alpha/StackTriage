"""Reproducible evaluation of window anomaly detection on labelled synthetic scenarios.

    python scripts/evaluate_anomaly.py              # 10 seeds, prints tables, exit 1 if a gate fails
    python scripts/evaluate_anomaly.py --dev        # the tuning seeds (never used for reported numbers)
    python scripts/evaluate_anomaly.py --seeds 3,4  # explicit seeds

Candidates: the statistical robust-z baseline and scikit-learn IsolationForest (refit from the
training windows on every run), plus a single-feature "ERROR count" rule as a sanity floor.
All fit on the first half of each timeline only and are scored on the later half
(the `early_incident` stress scenario is additionally scored on the training period).

Definitions (windows are 60 s; "evaluated" = the later period, windows >= eval_from):
  precision/recall/F1/FPR  per window, pooled over the main scenarios of one seed
  incident recall          share of labelled incidents with >= 1 flagged window inside them
  delay                    windows from incident start to the first flagged window (detected only)
  false episodes           runs of consecutive flagged windows that touch no labelled window;
                           FA/incident = false episodes per labelled incident
  ROC-AUC / PR-AUC         of the continuous score (pooled per seed; per scenario in the 2nd table)
mean +- std are over seeds (sample std).

Sanity gates (exit status 1 if any fails): the ERROR-count rule and every single feature must
not be perfect; no training window may lie in the evaluated period and the baseline must not
change when the later period is replaced; reruns must be identical; severity must not be a
monotone function of score and must use more than one level; the AUTO_METHOD constant must
equal the decision this script measures.
"""
from __future__ import annotations

import argparse
import copy
import statistics
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from make_ml_scenarios import (  # noqa: E402
    DEV_SEEDS, EVAL_SEEDS, MAIN_NAMES, SCENARIO_NAMES, TRAIN_PERIOD_NAMES, Scenario, make_scenario,
)
from sklearn.metrics import average_precision_score, precision_recall_curve, roc_auc_score  # noqa: E402

from stacktriage.anomaly import AUTO_METHOD, detect_anomalies  # noqa: E402
from stacktriage.anomaly.features import observed  # noqa: E402

METHODS = ("statistical", "isolation_forest")
RULE = "error_count_rule"
ALL_CANDIDATES = (*METHODS, RULE)
FEATURES = ("error_count", "event_rate", "error_ratio", "new_groups", "metric.cpu_pct", "metric.latency_ms",
            "metric.queue_depth")


@dataclass(slots=True)
class Run:
    """One candidate on one scenario, restricted to the evaluated period."""

    scenario: str
    group: str
    seed: int
    labels: list[bool]
    flags: list[bool]
    scores: list[float]
    incidents: list[tuple[int, int]]  # shifted to evaluated-period indices
    n_incidents_total: int
    train_excluded: int = 0
    full_flags: list[bool] = field(default_factory=list)  # whole timeline (early_incident)
    full_labels: list[bool] = field(default_factory=list)


def _runs(flags: list[bool]) -> list[tuple[int, int]]:
    out, i = [], 0
    while i < len(flags):
        if flags[i]:
            j = i
            while j + 1 < len(flags) and flags[j + 1]:
                j += 1
            out.append((i, j + 1))
            i = j + 1
        else:
            i += 1
    return out


def _mk_run(sc: Scenario, flags_full: list[bool], scores_full: list[float], **kw) -> Run:
    e = sc.eval_from
    inc = [(max(i.start, e) - e, i.end - e) for i in sc.incidents if i.end > e]
    return Run(
        sc.name, sc.group, sc.seed, sc.labels[e:], flags_full[e:], scores_full[e:], inc, len(inc),
        full_flags=flags_full, full_labels=sc.labels, **kw,
    )


def run_detector(sc: Scenario, method: str, seed: int):
    rep = detect_anomalies(sc.timeline, method=method, train_fraction=sc.train_fraction, seed=seed)
    flags = [w.is_anomalous for w in rep.windows]
    scores = [w.score for w in rep.windows]
    run = _mk_run(
        sc, flags, scores, train_excluded=rep.parameters.get("excluded_windows", 0),
    )
    return rep, run


def _in_eval(sc: Scenario, rep, ep) -> bool:
    return ep.start >= sc.timeline.buckets[sc.eval_from].start


def run_error_rule(sc: Scenario) -> Run:
    """Single-feature rule: ERROR count above (train mean + 3 std)."""
    counts = [b.errors for b in sc.timeline.buckets]
    train = counts[: int(len(counts) * sc.train_fraction)]
    thr = statistics.fmean(train) + 3 * (statistics.pstdev(train) if len(train) > 1 else 0.0)
    return _mk_run(sc, [c > thr for c in counts], [float(c) for c in counts])


# ---- metrics -------------------------------------------------------------------------------

def _prf(labels: list[bool], flags: list[bool]) -> dict[str, float]:
    tp = sum(1 for lab, f in zip(labels, flags) if lab and f)
    fp = sum(1 for lab, f in zip(labels, flags) if not lab and f)
    fn = sum(1 for lab, f in zip(labels, flags) if lab and not f)
    tn = len(labels) - tp - fp - fn
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    return {
        "precision": p, "recall": r, "f1": 2 * p * r / (p + r) if p + r else 0.0,
        "fpr": fp / (fp + tn) if fp + tn else 0.0, "fp": fp, "tp": tp,
    }


def _incident_stats(runs: list[Run]) -> dict[str, float]:
    detected = total = false_eps = eps = 0
    delays: list[int] = []
    for r in runs:
        for a, b in r.incidents:
            total += 1
            hit = [i for i in range(a, b) if r.flags[i]]
            if hit:
                detected += 1
                delays.append(hit[0] - a)
        for a, b in _runs(r.flags):
            eps += 1
            if not any(r.labels[a:b]):
                false_eps += 1
    return {
        "inc_recall": detected / total if total else float("nan"),
        "delay": statistics.fmean(delays) if delays else float("nan"),
        "false_eps": false_eps, "episodes": eps,
        "fa_per_inc": false_eps / total if total else float("nan"),
        "ep_precision": (eps - false_eps) / eps if eps else float("nan"),
        "detected": detected, "incidents": total,
    }


def _auc(labels: list[bool], scores: list[float]) -> tuple[float, float]:
    if not any(labels) or all(labels):
        return float("nan"), float("nan")
    return roc_auc_score(labels, scores), average_precision_score(labels, scores)


def pooled(runs: list[Run]) -> dict[str, float]:
    labels = [x for r in runs for x in r.labels]
    flags = [x for r in runs for x in r.flags]
    scores = [x for r in runs for x in r.scores]
    out = _prf(labels, flags)
    out.update(_incident_stats(runs))
    out["roc_auc"], out["pr_auc"] = _auc(labels, scores)
    p, r, _ = precision_recall_curve(labels, scores)
    out["oracle_f1"] = float(max((2 * a * b / (a + b) for a, b in zip(p, r) if a + b), default=0.0))
    return out


def _ms(vals: list[float], digits: int = 3) -> str:
    v = [x for x in vals if x == x]
    if not v:
        return "n/a"
    sd = statistics.stdev(v) if len(v) > 1 else 0.0
    return f"{statistics.fmean(v):.{digits}f} ± {sd:.{digits}f}"


# ---- gates ---------------------------------------------------------------------------------

class Gates:
    def __init__(self) -> None:
        self.failures: list[str] = []
        self.passed: list[str] = []

    def check(self, ok: bool, text: str) -> None:
        (self.passed if ok else self.failures).append(text)


def leakage_checks(gates: Gates, sc: Scenario, method: str, rep, replace_later: bool) -> None:
    tl = sc.timeline
    s = int(len(tl.buckets) * sc.train_fraction)  # first window of the later period
    tag = f"[{sc.name} s{sc.seed} {method}]"
    if rep.train_range is None:  # too short to train: must degrade to "no findings" with a note
        gates.check(not any(w.is_anomalous for w in rep.windows) and bool(rep.notes),
                    f"{tag} too few windows: no findings and an explanatory note")
        return
    ok_split = rep.train_range[1] <= tl.buckets[s].start
    ok_split = ok_split and not any(w.used_for_training for w in rep.windows[s:])
    gates.check(ok_split, f"{tag} training range ends before the later period")
    if sc.group == "main":
        for inc in sc.incidents:
            gates.check(
                rep.train_range is not None and tl.buckets[inc.start].start >= rep.train_range[1],
                f"{tag} incident {inc.kind} starts after the training range",
            )
    if not replace_later:
        return
    # Replace the later period by something very different: the fitted baseline, the threshold
    # and the scores of the training period must not move (nothing after the split is used to fit).
    tl2 = copy.deepcopy(tl)
    for b in tl2.buckets[s:]:
        b.total, b.error, b.critical, b.new_groups = b.total * 7 + 3, b.error * 5 + 9, 0, b.new_groups + 4
        b.metrics = {k: v * 3.0 for k, v in b.metrics.items()}
    rep2 = detect_anomalies(tl2, method=method, train_fraction=sc.train_fraction, seed=sc.seed)
    same = (
        rep.parameters.get("baseline") == rep2.parameters.get("baseline")
        and rep.parameters.get("threshold_raw") == rep2.parameters.get("threshold_raw")
        and [w.score for w in rep.windows[:s]] == [w.score for w in rep2.windows[:s]]
    )
    gates.check(same, f"{tag} baseline, threshold and training-period scores independent of the later period")


# ---- main ----------------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dev", action="store_true", help="use the tuning seeds instead of the reported ones")
    ap.add_argument("--seeds", help="comma separated seeds")
    args = ap.parse_args()
    seeds = tuple(int(s) for s in args.seeds.split(",")) if args.seeds else (DEV_SEEDS if args.dev else EVAL_SEEDS)
    t0 = time.time()
    gates = Gates()

    runs: dict[tuple[str, str, int], Run] = {}  # (candidate, scenario, seed)
    reports: dict[tuple[str, str, int], object] = {}
    scenarios: dict[tuple[str, int], Scenario] = {}
    for seed in seeds:
        for name in SCENARIO_NAMES:
            sc = make_scenario(name, seed)
            scenarios[(name, seed)] = sc
            for m in METHODS:
                rep, run = run_detector(sc, m, seed)
                runs[(m, name, seed)] = run
                reports[(m, name, seed)] = rep
                leakage_checks(gates, sc, m, rep, replace_later=seed == seeds[0])
            runs[(RULE, name, seed)] = run_error_rule(sc)

    # determinism: regenerate and rerun the first seed
    for name in SCENARIO_NAMES:
        sc2 = make_scenario(name, seeds[0])
        sc1 = scenarios[(name, seeds[0])]
        same_tl = [(b.total, b.errors, b.new_groups, b.metrics) for b in sc1.timeline.buckets] == [
            (b.total, b.errors, b.new_groups, b.metrics) for b in sc2.timeline.buckets] and sc1.labels == sc2.labels
        gates.check(same_tl, f"[{name}] scenario generation deterministic")
        for m in METHODS:
            rep2 = detect_anomalies(sc2.timeline, method=m, train_fraction=sc2.train_fraction, seed=seeds[0])
            gates.check(rep2.to_dict() == reports[(m, name, seeds[0])].to_dict(), f"[{name} {m}] rerun identical")

    # ---- table 1: main scenarios, pooled per seed -------------------------------------------
    print(f"\nseeds: {list(seeds)}   scenarios (main): {', '.join(MAIN_NAMES)}")
    per_seed = {c: [pooled([runs[(c, n, s)] for n in MAIN_NAMES]) for s in seeds] for c in ALL_CANDIDATES}
    cols = [("precision", "precision"), ("recall", "recall"), ("f1", "F1"), ("fpr", "FPR/window"),
            ("inc_recall", "incident recall"), ("delay", "delay (win)"), ("fa_per_inc", "FA/incident"),
            ("ep_precision", "episode prec."), ("roc_auc", "ROC-AUC"), ("pr_auc", "PR-AUC"),
            ("oracle_f1", "best F1 (oracle threshold)")]
    print("\n### Main scenarios, pooled per seed, later period only (mean ± std over seeds)\n")
    print("| candidate | " + " | ".join(c[1] for c in cols) + " |")
    print("|---|" + "---|" * len(cols))
    for c in ALL_CANDIDATES:
        print(f"| {c} | " + " | ".join(_ms([d[k] for d in per_seed[c]], 2 if k == "delay" else 3) for k, _ in cols) + " |")

    # ---- single-feature ceilings -------------------------------------------------------------
    print("\n### Single-feature sanity (pooled main scenarios, later period): ROC-AUC of the raw feature\n")
    print("| feature | ROC-AUC mean ± std | best-possible F1 of a threshold tuned on the labels |")
    print("|---|---|---|")
    for f in FEATURES:
        aucs, bests = [], []
        for s in seeds:
            labels, vals = [], []
            for n in MAIN_NAMES:
                sc = scenarios[(n, s)]
                for b, lab in zip(sc.timeline.buckets[sc.eval_from:], sc.labels[sc.eval_from:]):
                    labels.append(lab)
                    vals.append(observed(b).get(f, float("nan")))
            vals = [0.0 if v != v else v for v in vals]
            auc_hi = _auc(labels, vals)[0]
            auc_lo = _auc(labels, [-v for v in vals])[0]
            aucs.append(max(auc_hi, auc_lo))
            best = 0.0
            for t in sorted(set(vals)):
                for sign in (1, -1):
                    fl = [(sign * v) > (sign * t) for v in vals]
                    best = max(best, _prf(labels, fl)["f1"])
            bests.append(best)
        print(f"| {f} | {_ms(aucs)} | {_ms(bests)} |")
        gates.check(all(a < 0.999 for a in aucs), f"single feature {f} is not a perfect separator (AUC<1)")

    rule = per_seed[RULE]
    gates.check(
        not any(d["precision"] == 1 and d["recall"] == 1 for d in rule),
        "single-feature ERROR-count rule is not perfect in any seed",
    )
    gates.check(statistics.fmean(d["f1"] for d in rule) < 0.9, "single-feature ERROR-count rule mean F1 < 0.9")

    # ---- table 2: per scenario -----------------------------------------------------------------
    print("\n### Per scenario (mean over seeds; later period unless noted)\n")
    print("| scenario | candidate | incidents detected | window recall | delay (win) | FP windows/seed | FP episodes/seed | ROC-AUC | PR-AUC |")
    print("|---|---|---|---|---|---|---|---|---|")
    for n in SCENARIO_NAMES:
        for c in ALL_CANDIDATES:
            rs = [runs[(c, n, s)] for s in seeds]
            st = [_incident_stats([r]) for r in rs]
            pr = [_prf(r.labels, r.flags) for r in rs]
            au = [_auc(r.labels, r.scores) for r in rs]
            det, tot = sum(x["detected"] for x in st), sum(x["incidents"] for x in st)
            det_s = f"{det}/{tot}" if tot else "-"
            rec = _ms([p["recall"] for p in pr], 2) if tot else "-"
            print(
                f"| {n} | {c} | {det_s} | {rec} | {_ms([x['delay'] for x in st], 1)} | "
                f"{statistics.fmean(p['fp'] for p in pr):.2f} | {statistics.fmean(x['false_eps'] for x in st):.2f} | "
                f"{_ms([a[0] for a in au], 2)} | {_ms([a[1] for a in au], 2)} |"
            )

    # ---- stress details --------------------------------------------------------------------------
    print("\n### Incidents inside the training period (scored on all windows, baseline fitted on the first half)\n")
    print("| scenario | candidate | incident-window recall | windows excluded from baseline | later-period false windows |")
    print("|---|---|---|---|---|")
    for n in TRAIN_PERIOD_NAMES:
        for m in METHODS:
            rs = [runs[(m, n, s)] for s in seeds]
            rec = [sum(1 for lab, f in zip(r.full_labels, r.full_flags) if lab and f) / max(1, sum(r.full_labels))
                   for r in rs]
            half = [int(len(r.full_flags) * 0.5) for r in rs]
            later_fp = [sum(1 for i in range(h, len(r.full_flags)) if r.full_flags[i] and not r.full_labels[i])
                        for r, h in zip(rs, half)]
            print(f"| {n} | {m} | {_ms(rec, 2)} | {_ms([r.train_excluded for r in rs], 1)} | {_ms(later_fp, 2)} |")

    # ---- severity sanity -------------------------------------------------------------------------
    print("\n### Severity of episodes (evaluated period), by whether the episode touches a labelled incident\n")
    print("| candidate | outcome | low | medium | high | critical |")
    print("|---|---|---|---|---|---|")
    all_eps: list[tuple[float, str]] = []
    for m in METHODS:
        tp_sev: list[str] = []
        fp_sev: list[str] = []
        for (c, n, s), r in runs.items():
            if c != m or n in TRAIN_PERIOD_NAMES:
                continue
            rep = reports[(c, n, s)]
            sc = scenarios[(n, s)]
            for ep in rep.episodes:
                if not _in_eval(sc, rep, ep):
                    continue
                a = int((ep.start - sc.timeline.buckets[0].start).total_seconds() // sc.timeline.window_seconds)
                touches = any(sc.labels[a:a + ep.window_count])
                (tp_sev if touches else fp_sev).append(ep.severity)
                all_eps.append((ep.peak_score, ep.severity))
        for label, sev in (("true incident", tp_sev), ("false alarm", fp_sev)):
            print(f"| {m} | {label} | " + " | ".join(str(sev.count(k)) for k in ("low", "medium", "high", "critical")) + " |")
    order = {"low": 0, "medium": 1, "high": 2, "critical": 3}
    levels = {sev for _, sev in all_eps}
    gates.check(len(levels) >= 3, f"severity uses >= 3 distinct levels (saw {sorted(levels)})")
    inversion = any(
        a[0] > b[0] and order[a[1]] < order[b[1]] for a in all_eps for b in all_eps
    )
    gates.check(inversion, "severity is not a monotone function of the anomaly score (inversion found)")

    # ---- anatomy of false alarms ------------------------------------------------------------------------
    print("\n### False-alarm windows of the statistical baseline (main scenarios, later period, all seeds)\n")
    by_feature: dict[str, int] = {}
    by_class: dict[str, int] = {}
    for n in MAIN_NAMES:
        for s in seeds:
            sc, rep = scenarios[(n, s)], reports[("statistical", n, s)]
            for i in range(sc.eval_from, len(rep.windows)):
                w = rep.windows[i]
                if w.is_anomalous and not sc.labels[i]:
                    top = w.contributions[0]["feature"] if w.contributions else "none"
                    by_feature[top] = by_feature.get(top, 0) + 1
                    by_class[w.classification] = by_class.get(w.classification, 0) + 1
    print("top contributing feature: " + ", ".join(f"{k} {v}" for k, v in sorted(by_feature.items(), key=lambda kv: -kv[1])))
    print("classification:           " + ", ".join(f"{k} {v}" for k, v in sorted(by_class.items(), key=lambda kv: -kv[1])))

    # ---- decision ------------------------------------------------------------------------------------
    f1_stat = [d["f1"] for d in per_seed["statistical"]]
    f1_if = [d["f1"] for d in per_seed["isolation_forest"]]
    margin = max(statistics.stdev(f1_stat), statistics.stdev(f1_if)) if len(seeds) > 1 else 0.0
    gain = statistics.fmean(f1_if) - statistics.fmean(f1_stat)
    decision = "isolation_forest" if gain > margin else "statistical"
    print(
        f"\nDecision rule: isolation_forest becomes the default only if its mean pooled F1 exceeds the "
        f"statistical baseline's by more than the larger std over seeds.\n"
        f"  F1 isolation_forest {statistics.fmean(f1_if):.3f} - statistical {statistics.fmean(f1_stat):.3f} = "
        f"{gain:+.3f} (margin {margin:.3f})  ->  default = {decision}   (AUTO_METHOD = {AUTO_METHOD})"
    )
    gates.check(AUTO_METHOD == decision, f"AUTO_METHOD ({AUTO_METHOD}) equals the measured decision ({decision})")

    print(f"\nGates: {len(gates.passed)} passed, {len(gates.failures)} failed   ({time.time() - t0:.0f}s)")
    for f in gates.failures:
        print("  FAIL:", f)
    return 1 if gates.failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
