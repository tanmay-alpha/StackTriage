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


def _run_ops(args) -> int:
    import os

    db = getattr(args, "db", None) or os.environ.get("STACKTRIAGE_DB", "data/stacktriage.db")
    if args.cmd == "serve":
        try:
            import uvicorn
        except ImportError:
            print("error: serving needs the api extra: pip install 'stacktriage[api]'", file=sys.stderr)
            return 2
        uvicorn.run("stacktriage.api.app:app_factory", factory=True, host=args.host, port=args.port)
        return 0
    from .db import maintenance as m

    try:
        if args.cmd == "backup":
            m.backup(db, args.out, overwrite=args.force)
            print(f"backup written and verified: {args.out}")
        elif args.cmd == "restore":
            safety = m.restore(args.backup, db)
            print(f"restored {db} from {args.backup}" + (f" (previous copy: {safety})" if safety else ""))
        else:
            problems = m.check(db)
            print("ok" if not problems else "\n".join(problems))
            return 0 if not problems else 1
    except m.MaintenanceError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="stacktriage")
    sub = ap.add_subparsers(dest="cmd", required=True)
    an = sub.add_parser("analyze", help="parse a log file and print grouped errors")
    an.add_argument("path")
    an.add_argument("--format", choices=["python", "node", "java", "jsonl", "csv", "text"])
    an.add_argument("--service", help="service name for records that carry none")
    an.add_argument("--window", type=int, help="timeline window in seconds (default: automatic)")
    an.add_argument("--json", action="store_true", help="machine-readable output")
    sv = sub.add_parser("serve", help="run the API (and the built web UI, if present)")
    sv.add_argument("--host", default="127.0.0.1")
    sv.add_argument("--port", type=int, default=8000)
    bk = sub.add_parser("backup", help="write a verified copy of the database")
    bk.add_argument("out")
    bk.add_argument("--db", default=None)
    bk.add_argument("--force", action="store_true")
    rs = sub.add_parser("restore", help="restore the database from a backup (stop the server first)")
    rs.add_argument("backup")
    rs.add_argument("--db", default=None)
    ck = sub.add_parser("check", help="run integrity checks on the database")
    ck.add_argument("--db", default=None)
    args = ap.parse_args(argv)

    if args.cmd in ("serve", "backup", "restore", "check"):
        return _run_ops(args)

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
