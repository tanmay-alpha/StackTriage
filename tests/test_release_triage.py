"""Release-aware triage: a recorded fix time/version turns 'resolved but observed' into a checkable regression."""
import csv
import io
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from stacktriage.api import Settings, create_app
from stacktriage.db.engine import MIGRATIONS

from test_api import client, upload  # noqa: F401  (client is a pytest fixture)

# deploy_after.log covers 2026-03-05 12:00-13:00 UTC; deploy_before.log 10:00-11:00 UTC
DEPLOY = "2026-03-05T11:30:00Z"


def _pair(c):
    return upload(c, "deploy_before.log").json()["id"], upload(c, "deploy_after.log").json()["id"]


def _items(c, b, t, **params):
    r = c.get(f"/api/imports/{b}/compare/{t}", params={"limit": 200, **params})
    assert r.status_code == 200, r.text
    return r.json()


def _fp(data, prefix):
    return next(i["fingerprint"] for i in data["items"] if i["title"].startswith(prefix))


def _row(data, fp):
    return next(i for i in data["items"] if i["fingerprint"] == fp)


def test_status_api_records_and_clears_fix(client):
    b, t = _pair(client)
    fp = _fp(_items(client, b, t), "ConnectionError")
    r = client.put(f"/api/incidents/{fp}/status", json={"status": "resolved", "fixed_at": DEPLOY, "fixed_version": " 2.4.1 "})
    assert r.status_code == 200
    assert r.json() == {"status": "resolved", "fixed_at": "2026-03-05T11:30:00Z", "fixed_version": "2.4.1"}
    detail = client.get(f"/api/imports/{t}/incidents/{fp}").json()
    assert (detail["fixed_at"], detail["fixed_version"]) == ("2026-03-05T11:30:00Z", "2.4.1")
    # re-resolving without values keeps the record; another status clears it
    assert client.put(f"/api/incidents/{fp}/status", json={"status": "resolved"}).json()["fixed_version"] == "2.4.1"
    assert client.put(f"/api/incidents/{fp}/status", json={"status": "investigating"}).json() == {
        "status": "investigating", "fixed_at": None, "fixed_version": None}
    assert client.put(f"/api/incidents/{fp}/status", json={"status": "resolved"}).json()["fixed_at"] is None
    # explicit null clears one field
    client.put(f"/api/incidents/{fp}/status", json={"status": "resolved", "fixed_at": DEPLOY, "fixed_version": "x"})
    assert client.put(f"/api/incidents/{fp}/status", json={"status": "resolved", "fixed_version": None}).json() == {
        "status": "resolved", "fixed_at": "2026-03-05T11:30:00Z", "fixed_version": None}


@pytest.mark.parametrize("body", [
    {"status": "open", "fixed_at": DEPLOY},
    {"status": "investigating", "fixed_version": "1.0"},
    {"status": "resolved", "fixed_at": "0001-01-01T00:00:00Z"},
    {"status": "resolved", "fixed_version": "v" * 65},
    {"status": "resolved", "fixed_at": "not a time"},
    {"status": "resolved", "fixed_by": "me"},
])
def test_invalid_fix_records_are_rejected(client, body):
    b, t = _pair(client)
    fp = _fp(_items(client, b, t), "ConnectionError")
    assert client.put(f"/api/incidents/{fp}/status", json=body).status_code == 422
    assert _row(_items(client, b, t), fp)["status"] == "open"  # nothing was written


def test_comparison_fix_checks(client):
    b, t = _pair(client)
    data = _items(client, b, t)
    inventory, payment, cache = _fp(data, "ConnectionError"), _fp(data, "TimeoutError"), _fp(data, "Cache entry")
    legacy = _fp(data, "Deprecated endpoint")
    put = lambda fp, **k: client.put(f"/api/incidents/{fp}/status", json={"status": "resolved", **k})  # noqa: E731
    put(inventory, fixed_at=DEPLOY, fixed_version="2.4.1")      # fix before the target capture: every occurrence is after it
    put(payment, fixed_at="2026-03-05T12:30:00Z")                # fix in the middle of the target capture
    put(cache, fixed_at="2026-03-05T14:00:00Z")                  # fix after the target capture ends
    put(legacy, fixed_at=DEPLOY)                                 # absent from the target: no check
    data = _items(client, b, t)
    inv, pay, cac, leg = (_row(data, fp) for fp in (inventory, payment, cache, legacy))
    assert (inv["fix_check"], inv["occurrences_after_fix"], inv["fixed_version"]) == ("regressed", 40, "2.4.1")
    assert pay["fix_check"] == "regressed" and 0 < pay["occurrences_after_fix"] < pay["target"]["count"]
    assert (cac["fix_check"], cac["occurrences_after_fix"]) == ("before_fix", 0)
    assert leg["fix_check"] is None and leg["resolved_observed"] is False
    assert data["totals"]["regressed"] == 2 and data["totals"]["resolved_observed"] == 3
    # regressions sort first and have their own filter
    assert {i["fingerprint"] for i in data["items"][:2]} == {inventory, payment}
    only = _items(client, b, t, category="regressed")
    assert {i["fingerprint"] for i in only["items"]} == {inventory, payment} and only["total"] == 2


def test_occurrences_after_fix_counts_exactly(client):
    """Count is strictly after the fix instant, from stored event timestamps."""
    before = b"2026-03-05 10:00:00,000 - app - ERROR - boom\n" * 3
    after = b"".join(f"2026-03-05 12:00:{s:02d},000 - app - ERROR - boom\n".encode() for s in range(10))
    ib = client.post("/api/imports", params={"filename": "a.log"}, content=before).json()["id"]
    it = client.post("/api/imports", params={"filename": "b.log"}, content=after).json()["id"]
    fp = _items(client, ib, it)["items"][0]["fingerprint"]
    client.put(f"/api/incidents/{fp}/status", json={"status": "resolved", "fixed_at": "2026-03-05T12:00:04Z"})
    row = _items(client, ib, it)["items"][0]
    assert (row["fix_check"], row["occurrences_after_fix"]) == ("regressed", 5)  # seconds 05..09
    client.put(f"/api/incidents/{fp}/status", json={"status": "resolved", "fixed_at": "2026-03-05T12:00:09Z"})
    assert _items(client, ib, it)["items"][0]["fix_check"] == "before_fix"


def test_no_fix_time_and_untimed_target(client):
    timed = b"2026-03-05 10:00:00,000 - app - ERROR - boom\n"
    ib = client.post("/api/imports", params={"filename": "a.log"}, content=timed).json()["id"]
    it = client.post("/api/imports", params={"filename": "u.jsonl"},
                     content=b'{"level":"error","message":"boom"}\n').json()["id"]
    items = _items(client, ib, it)["items"]
    assert len(items) == 1, "the two formats must share the fingerprint for this test"
    fp = items[0]["fingerprint"]
    client.put(f"/api/incidents/{fp}/status", json={"status": "resolved"})
    assert _items(client, ib, it)["items"][0]["fix_check"] == "no_fix_time"
    client.put(f"/api/incidents/{fp}/status", json={"status": "resolved", "fixed_at": DEPLOY})
    assert _items(client, ib, it)["items"][0]["fix_check"] == "untimed"


def test_exports_include_fix_check(client):
    b, t = _pair(client)
    fp = _fp(_items(client, b, t), "ConnectionError")
    client.put(f"/api/incidents/{fp}/status", json={"status": "resolved", "fixed_at": DEPLOY, "fixed_version": "=2.4.1"})
    rows = list(csv.DictReader(io.StringIO(client.get(f"/api/imports/{b}/compare/{t}/export", params={"format": "csv"}).text)))
    row = next(r for r in rows if r["fingerprint"] == fp)
    assert (row["fix_check"], row["occurrences_after_fix"], row["fixed_version"]) == ("regressed", "40", "'=2.4.1")
    md = client.get(f"/api/imports/{b}/compare/{t}/export", params={"format": "md"}).text
    assert "regressed: 40 after fix =2.4.1" in md
    js = client.get(f"/api/imports/{b}/compare/{t}/export", params={"format": "json"}).json()
    assert js["totals"]["regressed"] == 1


def test_v1_database_is_migrated_and_keeps_triage(tmp_path):
    db = tmp_path / "old.db"
    c = TestClient(create_app(Settings(db_path=str(db))))
    data = (Path(__file__).parent / "fixtures" / "spring_boot.log").read_bytes()
    iid = c.post("/api/imports", params={"filename": "s.log"}, content=data,
                 headers={"Content-Type": "application/octet-stream"}).json()["id"]
    fp = c.get(f"/api/imports/{iid}/incidents").json()["items"][0]["fingerprint"]
    c.put(f"/api/incidents/{fp}/status", json={"status": "resolved"})
    c.app.state.engine.dispose()
    con = sqlite3.connect(db)  # turn it back into a schema-v1 database
    con.execute("ALTER TABLE triage DROP COLUMN fixed_ms")
    con.execute("ALTER TABLE triage DROP COLUMN fixed_version")
    con.execute("PRAGMA user_version = 1")
    con.commit()
    con.close()

    c2 = TestClient(create_app(Settings(db_path=str(db))))
    con = sqlite3.connect(db)
    assert con.execute("PRAGMA user_version").fetchone()[0] == len(MIGRATIONS) == 2
    assert {"fixed_ms", "fixed_version"} <= {r[1] for r in con.execute("PRAGMA table_info(triage)")}
    con.close()
    d = c2.get(f"/api/imports/{iid}/incidents/{fp}").json()
    assert d["status"] == "resolved" and d["fixed_at"] is None
    assert c2.put(f"/api/incidents/{fp}/status", json={"status": "resolved", "fixed_version": "1.2"}).json()["fixed_version"] == "1.2"
    c2.app.state.engine.dispose()
