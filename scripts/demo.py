"""Import checked-in synthetic fixtures into a running StackTriage API.

Run from any directory with Python 3.11+: python scripts/demo.py
Uses STACKTRIAGE_API_KEY when set. Does not delete imports or change triage state.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures"


def request_json(base: str, path: str, key: str | None, data: bytes | None = None) -> dict:
    headers = {"Accept": "application/json"}
    if key:
        headers["X-API-Key"] = key
    if data is not None:
        headers["Content-Type"] = "application/octet-stream"
    request = Request(base + path, data=data, headers=headers)
    with urlopen(request, timeout=60) as response:
        return json.load(response)


def run(base: str, key: str | None) -> None:
    """Fail on fixture-count or duplicate-detection mismatches; retain demo data."""
    for name in ("spring_boot", "telemetry"):
        expected = json.loads((FIXTURES / "expected" / f"{name}.json").read_text(encoding="utf-8"))
        filename = expected["file"]
        data = (FIXTURES / filename).read_bytes()
        path = "/api/imports?" + urlencode({"filename": filename})
        summary = request_json(base, path, key, data)
        actual = (summary["format"], summary["event_count"], summary["incident_count"])
        wanted = (expected["format"], expected["total_events"], len(expected["groups"]))
        if actual != wanted:
            raise RuntimeError(f"{filename}: expected {wanted}, received {actual}")
        duplicate = request_json(base, path, key, data)
        if not duplicate["duplicate"] or duplicate["id"] != summary["id"]:
            raise RuntimeError(f"{filename}: duplicate detection did not reuse the import")
        state = "reused" if summary["duplicate"] else "created"
        print(f"{filename}: import {summary['id']} {state}; {actual[1]} events, "
              f"{actual[2]} groups; duplicate verified")
        print(f"  API detail: {base}/api/imports/{summary['id']}")
    print(f"Open {base}/ in the browser; imports remain available for the walkthrough.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8000", help="running backend base URL")
    args = parser.parse_args(argv)
    try:
        run(args.url.rstrip("/"), os.environ.get("STACKTRIAGE_API_KEY"))
    except HTTPError as exc:
        print(f"Demo failed: HTTP {exc.code}; check the server URL and API key.", file=sys.stderr)
        return 1
    except (OSError, URLError, ValueError, KeyError, RuntimeError) as exc:
        print(f"Demo failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
