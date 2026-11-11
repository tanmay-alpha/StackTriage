"""End-to-end check of the production Docker image with a named volume at /data.

Usage: python scripts/docker_verify.py [--image stacktriage:verify] [--port 18000]
Needs a running Docker daemon. Uses a generated API key and the synthetic fixtures; removes its
containers and volume on exit. Exit code 0 only if every check passes.
"""
from __future__ import annotations

import argparse
import json
import secrets
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures"
CANARY = "canarysecrettoken" + secrets.token_hex(8)
failures: list[str] = []
harvested: list[str] = []  # stdout+stderr of every container, collected before it is removed


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" - {detail}" if detail and not ok else ""))
    if not ok:
        failures.append(name)


def harvest(name: str) -> None:
    p = docker("logs", name, check_rc=False)
    harvested.append(p.stdout + p.stderr)


def docker(*args: str, check_rc: bool = True) -> subprocess.CompletedProcess:
    p = subprocess.run(["docker", *args], capture_output=True, text=True, encoding="utf-8", errors="replace")
    if check_rc and p.returncode != 0:
        raise RuntimeError(f"docker {' '.join(args[:2])} failed: {p.stderr.strip()[:500]}")
    return p


class Api:
    def __init__(self, base: str, key: str | None):
        self.base, self.key = base, key

    def call(self, method: str, path: str, body: bytes | None = None, ctype: str | None = None, key: str | None = "default"):
        headers = {}
        k = self.key if key == "default" else key
        if k:
            headers["X-API-Key"] = k
        if ctype:
            headers["Content-Type"] = ctype
        try:
            with urlopen(Request(self.base + path, data=body, headers=headers, method=method), timeout=60) as r:
                raw = r.read()
                return r.status, (json.loads(raw) if raw else None)
        except HTTPError as e:
            return e.code, None

    def wait_healthy(self, timeout: float = 60) -> bool:
        end = time.time() + timeout
        while time.time() < end:
            try:
                with urlopen(self.base + "/api/health", timeout=3) as r:
                    if r.status == 200:
                        return True
            except (URLError, OSError):
                pass
            time.sleep(1)
        return False


def paged(api: Api, path: str) -> list:
    items, offset = [], 0
    while True:
        s, page = api.call("GET", f"{path}{'&' if '?' in path else '?'}limit=200&offset={offset}")
        if s != 200:
            raise RuntimeError(f"GET {path} -> {s}")
        items += page["items"]
        offset += 200
        if offset >= page["total"]:
            return items


def snapshot(api: Api) -> dict:
    """Full user-visible state: import details, every event row, every incident detail (status, notes, traces)."""
    snap = {}
    for imp in paged(api, "/api/imports"):
        _, detail = api.call("GET", f"/api/imports/{imp['id']}")
        incidents = {i["fingerprint"]: api.call("GET", f"/api/imports/{imp['id']}/incidents/{i['fingerprint']}")[1]
                     for i in paged(api, f"/api/imports/{imp['id']}/incidents")}
        snap[imp["id"]] = {"summary": imp, "detail": detail, "events": paged(api, f"/api/imports/{imp['id']}/events"),
                           "incidents": incidents}
    return snap


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", default="stacktriage:verify")
    ap.add_argument("--port", type=int, default=18000)
    a = ap.parse_args()
    key = secrets.token_urlsafe(24)
    suffix = secrets.token_hex(3)
    vol, name = f"stacktriage-verify-{suffix}", f"stacktriage-verify-{suffix}"
    base = f"http://127.0.0.1:{a.port}"
    api = Api(base, key)

    def run_container(env_key: str | None = key) -> None:
        cmd = ["run", "-d", "--name", name, "-p", f"{a.port}:8000", "-v", f"{vol}:/data"]
        if env_key:
            cmd += ["-e", f"STACKTRIAGE_API_KEY={env_key}"]
        docker(*cmd, a.image)
        check("server healthy", api.wait_healthy(), docker("logs", name, check_rc=False).stdout[-300:])

    try:
        docker("volume", "create", vol)
        # 1. production refuses to start without a key
        p = docker("run", "--rm", a.image, check_rc=False)
        check("refuses to start without API key", p.returncode != 0 and "STACKTRIAGE_API_KEY" in (p.stderr + p.stdout))
        p = docker("run", "--rm", "-e", "STACKTRIAGE_API_KEY=short", a.image, check_rc=False)
        check("refuses key shorter than 16 chars", p.returncode != 0)

        # 2. start with key
        run_container()
        uid = docker("exec", name, "id", "-u").stdout.strip()
        check("runs as non-root uid 10001", uid == "10001", uid)
        with urlopen(base + "/") as r:
            check("GET / returns HTML UI", r.status == 200 and "text/html" in r.headers.get("content-type", ""))
        status, h = api.call("GET", "/api/health", key=None)
        check("/api/health ok without credentials", status == 200 and h["status"] == "ok" and h["auth_required"] is True)

        # 3. auth
        for path in ("/api/imports", "/api/overview", "/api/imports/1/export?format=csv"):
            s, _ = api.call("GET", path, key=None)
            check(f"unauthenticated {path} rejected", s == 401, str(s))
        s, _ = api.call("GET", "/api/imports", key="wrong-key-wrong-key-wrong")
        check("wrong key rejected", s == 401, str(s))

        # 4. imports of the synthetic fixtures, counts vs ground truth
        for stem in ("spring_boot", "telemetry", "node_express"):
            exp = json.loads((FIXTURES / "expected" / f"{stem}.json").read_text(encoding="utf-8"))
            data = (FIXTURES / exp["file"]).read_bytes()
            s, sm = api.call("POST", "/api/imports?" + urlencode({"filename": exp["file"]}), data, "application/octet-stream")
            ok = s == 201 and (sm["format"], sm["event_count"], sm["incident_count"]) == (exp["format"], exp["total_events"], len(exp["groups"]))
            check(f"import {exp['file']} matches ground truth", ok, str(sm and (sm["format"], sm["event_count"], sm["incident_count"])))
            s2, dup = api.call("POST", "/api/imports?" + urlencode({"filename": exp["file"]}), data, "application/octet-stream")
            check(f"duplicate {exp['file']} reuses import", sm is not None and dup is not None and dup["duplicate"] and dup["id"] == sm["id"])

        # canary secret import: must be redacted everywhere
        canary_log = (f"2026-03-05 10:00:00,000 ERROR app.auth: login failed Authorization: Bearer {CANARY}\n"
                      f"2026-03-05 10:00:01,000 ERROR app.auth: login failed password={CANARY}x\n").encode()
        s, csm = api.call("POST", "/api/imports?filename=canary.log", canary_log, "application/octet-stream")
        check("canary import accepted", s == 201, str(s))
        s, ev = api.call("GET", f"/api/imports/{csm['id']}/events?limit=50")
        check("canary events returned", s == 200 and ev["total"] == 2 and len(ev["items"]) == 2, str(s))
        check("canary secret not stored/returned in events", ev is not None and CANARY not in json.dumps(ev))

        # 5. triage state
        _, imports = api.call("GET", "/api/imports?limit=100")
        spring = next(i for i in imports["items"] if i["filename"] == "spring_boot.log")
        _, inc = api.call("GET", f"/api/imports/{spring['id']}/incidents")
        fp = inc["items"][0]["fingerprint"]
        s, _ = api.call("PUT", f"/api/incidents/{fp}/status", json.dumps({"status": "investigating"}).encode(), "application/json")
        check("status update accepted", s == 200, str(s))
        s, _ = api.call("POST", f"/api/incidents/{fp}/notes", json.dumps({"body": "synthetic note: check pool size"}).encode(), "application/json")
        check("note created", s == 201, str(s))
        before = snapshot(api)
        check("snapshot has 4 imports", len(before) == 4, str(len(before)))
        check("status+note visible in snapshot", before[spring["id"]]["incidents"][fp]["status"] == "investigating"
              and [n["body"] for n in before[spring["id"]]["incidents"][fp]["notes"]] == ["synthetic note: check pool size"])

        # 6. restart persistence
        harvest(name)
        docker("restart", name)
        check("healthy after restart", api.wait_healthy())
        check("records identical after container restart", snapshot(api) == before)

        # 7. recreate with same volume
        harvest(name)
        docker("rm", "-f", name)
        run_container()
        check("records identical after container recreation (same volume)", snapshot(api) == before)

        # 8. backup + integrity (online), mutate, restore with server stopped
        bkp = docker("exec", name, "stacktriage", "backup", "/data/verify-backup.db", check_rc=False)
        check("backup command succeeds", bkp.returncode == 0, (bkp.stdout + bkp.stderr)[-300:])
        ck = docker("exec", name, "stacktriage", "check", check_rc=False)
        check("integrity check passes", ck.returncode == 0, (ck.stdout + ck.stderr)[-300:])
        s, _ = api.call("DELETE", f"/api/imports/{csm['id']}")
        check("delete import (mutation after backup)", s == 204, str(s))
        check("mutation visible", len(snapshot(api)) == 3)
        harvest(name)
        docker("stop", name)
        rs = docker("run", "--rm", "-v", f"{vol}:/data", a.image, "stacktriage", "restore", "/data/verify-backup.db", check_rc=False)
        check("restore command succeeds", rs.returncode == 0, (rs.stdout + rs.stderr)[-300:])
        docker("rm", "-f", name)
        run_container()
        check("restored database equals pre-mutation snapshot", snapshot(api) == before)
        ck = docker("exec", name, "stacktriage", "check", check_rc=False)
        check("integrity check passes after restore", ck.returncode == 0, (ck.stdout + ck.stderr)[-300:])

        # 9. logs leak nothing
        harvest(name)
        logs = "\n".join(harvested)
        check("container logs contain no API key", key not in logs)
        check("container logs contain no canary secret", CANARY not in logs)
        bk = docker("run", "--rm", "-v", f"{vol}:/data", "--entrypoint", "python", a.image, "-c",
                    "import sys;d=open('/data/verify-backup.db','rb').read();sys.exit(1 if sys.argv[1].encode() in d else 0)", CANARY, check_rc=False)
        check("backup file contains no canary secret", bk.returncode == 0)
    finally:
        docker("rm", "-f", name, check_rc=False)
        docker("volume", "rm", "-f", vol, check_rc=False)
    print(f"\n{'ALL CHECKS PASSED' if not failures else 'FAILED: ' + ', '.join(failures)}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
