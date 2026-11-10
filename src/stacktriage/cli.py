"""Command line: `python -m stacktriage analyze app.log`."""
from __future__ import annotations

import argparse
import json
import sys

from . import analyze, parse_file
from .models import UnsupportedFormatError


def _summary(path: str, fmt: str, diag: dict, a) -> dict:
    return {
        "file": path,
        "format": fmt,
        "diagnostics": diag,
        "total_events": a.total_events,
        "level_counts": a.level_counts,
        "service_counts": a.service_counts,
        "first_event": a.first_event.isoformat() if a.first_event else None,
        "last_event": a.last_event.isoformat() if a.last_event else None,
        "window_seconds": a.timeline.window_seconds,
        "groups": [
            {
                "fingerprint": g.fingerprint, "title": g.title, "service": g.service,
                "max_level": g.max_level.name, "count": g.count,
                "first_seen": g.first_seen.isoformat() if g.first_seen else None,
                "last_seen": g.last_seen.isoformat() if g.last_seen else None,
                "recurrence": a.activity[g.fingerprint].recurrence,
                "status": a.activity[g.fingerprint].status,
                "frame": g.frame,
            }
            for g in a.groups
        ],
        "notes": a.notes,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="stacktriage")
    sub = ap.add_subparsers(dest="cmd", required=True)
    an = sub.add_parser("analyze", help="parse a log file and print grouped errors")
    an.add_argument("path")
    an.add_argument("--format", choices=["python", "node", "java", "jsonl", "csv", "text"])
    an.add_argument("--service", help="service name for records that carry none")
    an.add_argument("--window", type=int, help="timeline window in seconds (default: automatic)")
    an.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args(argv)

    try:
        session, fh = parse_file(args.path, format=args.format, default_service=args.service)
    except UnsupportedFormatError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except OSError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    with fh:
        result = analyze(session, window_seconds=args.window)
    out = _summary(args.path, session.format, session.diagnostics.to_dict(), result)
    if args.json:
        json.dump(out, sys.stdout, indent=2)
        print()
        return 0
    print(f"{args.path}: {out['format']}, {out['total_events']} events, "
          f"{out['first_event']} -> {out['last_event']}")
    print("levels:", ", ".join(f"{k}={v}" for k, v in sorted(out["level_counts"].items())))
    for code, n in sorted(out["diagnostics"]["code_counts"].items()):
        print(f"  note {code}: {n}")
    print(f"{len(out['groups'])} error groups:")
    for g in out["groups"]:
        print(f"  {g['count']:>6}x [{g['max_level']}] {g['service']}: {g['title']}")
        print(f"          {g['first_seen']} .. {g['last_seen']}  {g['recurrence']}, {g['status']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
