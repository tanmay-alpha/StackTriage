"""Compare parser output with the injected ground truth for every fixture.

    python scripts/acceptance_report.py            # prints a table, exit 1 on any mismatch
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from stacktriage import analyze, parse  # noqa: E402

FIX = ROOT / "tests" / "fixtures"


def check(name: str) -> list[str]:
    exp = json.loads((FIX / "expected" / f"{name}.json").read_text(encoding="utf-8"))
    with open(FIX / exp["file"], "rb") as fh:
        sess = parse(fh, filename=exp["file"], default_service=exp["service"])
        res = analyze(sess)
    diag = sess.diagnostics
    problems: list[str] = []
    print(f"\n== {exp['file']}")
    print(f"   format   expected={exp['format']:<7} actual={sess.format}")
    print(f"   events   expected={exp['total_events']:<7} actual={res.total_events}")
    print(f"   groups   expected={len(exp['groups']):<7} actual={len(res.groups)}")
    if sess.format != exp["format"]:
        problems.append("format")
    if res.total_events != exp["total_events"]:
        problems.append("total_events")
    if len(res.groups) != len(exp["groups"]):
        problems.append("group_count")
    for e in exp["groups"]:
        cands = [g for g in res.groups if e["title_contains"] in g.title and (e["exc_type"] in (None, g.exc_type))]
        if len(cands) != 1:
            print(f"   {e['id']:<22} MISSING/AMBIGUOUS ({len(cands)} candidates)")
            problems.append(e["id"])
            continue
        g = cands[0]
        act = res.activity[g.fingerprint]
        first = g.first_seen.replace(microsecond=0).isoformat() if g.first_seen else None
        last = g.last_seen.replace(microsecond=0).isoformat() if g.last_seen else None
        ok = (g.count == e["count"] and first == e["first_second"] and last == e["last_second"]
              and g.max_level.name == e["level"] and (act.status == "ongoing") == e["ongoing"])
        print(f"   {e['id']:<22} count {e['count']:>3}/{g.count:<3} first {'ok' if first == e['first_second'] else 'BAD'} "
              f"last {'ok' if last == e['last_second'] else 'BAD'} level {e['level']}/{g.max_level.name} "
              f"ongoing {e['ongoing']}/{act.status == 'ongoing'} {'OK' if ok else 'MISMATCH'}")
        if not ok:
            problems.append(e["id"])
    if diag.redactions:
        print(f"   redactions: {diag.redactions}")
    return problems


if __name__ == "__main__":
    bad = {n: p for n in ["python_app", "node_express", "spring_boot", "pino_payments", "telemetry"] if (p := check(n))}
    print("\nRESULT:", "ALL MATCH" if not bad else f"MISMATCHES {bad}")
    sys.exit(1 if bad else 0)
