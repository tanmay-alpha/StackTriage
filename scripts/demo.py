"""Import checked-in synthetic fixtures into a running StackTriage API.

Run from any directory with Python 3.11+: python scripts/demo.py [--compare]
Uses STACKTRIAGE_API_KEY when set. Never deletes imports. The default run does not change triage
state; --compare marks one synthetic fixture group as resolved if it is still open (see run_compare).
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


def request_json(base: str, path: str, key: str | None, data: bytes | None = None, *, method: str | None = None,
                 content_type: str = "application/octet-stream") -> dict:
    headers = {"Accept": "application/json"}
    if key:
        headers["X-API-Key"] = key
    if data is not None:
        headers["Content-Type"] = content_type
    request = Request(base + path, data=data, headers=headers, method=method)
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


def run_compare(base: str, key: str | None) -> None:
    """Before/after-deployment demo: import the pair, simulate an earlier fix, compare, check ground truth."""
    truth = json.loads((FIXTURES / "expected" / "compare.json").read_text(encoding="utf-8"))
    ids = []
    for side in ("baseline", "target"):
        filename = truth[side]["file"]
        summary = request_json(base, "/api/imports?" + urlencode({"filename": filename}), key,
                               (FIXTURES / filename).read_bytes())
        if summary["event_count"] != truth[side]["total_events"]:
            raise RuntimeError(f"{filename}: expected {truth[side]['total_events']} events, got {summary['event_count']}")
        ids.append(summary["id"])
        print(f"{side}: {filename} -> import {summary['id']} ({summary['event_count']} events)")
    path = f"/api/imports/{ids[0]}/compare/{ids[1]}?limit=200"
    items = request_json(base, path, key)["items"]
    inventory = next(i for i in items if i["title"].startswith("ConnectionError: warehouse API"))
    if inventory["status"] == "open":  # never overwrite a status someone already set
        request_json(base, f"/api/incidents/{inventory['fingerprint']}/status", key,
                     json.dumps({"status": "resolved"}).encode(), method="PUT", content_type="application/json")
        print("Simulated an earlier fix: marked the warehouse ConnectionError group as resolved.")
    result = request_json(base, path, key)
    got = {((i["baseline"] or {}).get("count", 0), (i["target"] or {}).get("count", 0)): i for i in result["items"]}
    for g in truth["groups"]:
        item = got.get((g["baseline_count"], g["target_count"]))
        if item is None or (item["category"], item["change"]) != (g["category"], g["change"]):
            raise RuntimeError(f"{g['id']}: expected {g['category']}/{g['change']}, got "
                               f"{item and (item['category'], item['change'])}")
    t = result["totals"]
    print(f"Comparison: {t['new']} new, {t['increased']} increased, {t['decreased']} decreased, "
          f"{t['disappeared']} absent from target, {t['resolved_observed']} marked resolved but observed; "
          "matches the declared ground truth")
    print(f"Open {base}/compare?baseline={ids[0]}&target={ids[1]} in the browser.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8000", help="running backend base URL")
    parser.add_argument("--compare", action="store_true",
                        help="import the before/after-deployment pair and verify the comparison instead")
    args = parser.parse_args(argv)
    try:
        (run_compare if args.compare else run)(args.url.rstrip("/"), os.environ.get("STACKTRIAGE_API_KEY"))
    except HTTPError as exc:
        print(f"Demo failed: HTTP {exc.code}; check the server URL and API key.", file=sys.stderr)
        return 1
    except (OSError, URLError, ValueError, KeyError, RuntimeError, StopIteration) as exc:
        print(f"Demo failed: {exc!r}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
