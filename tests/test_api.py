"""API integration tests against a real (in-memory) SQLite database."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from stacktriage.api import Settings, create_app

FIX = Path(__file__).parent / "fixtures"
KEY = "k" * 24


def make_client(**kw) -> TestClient:
    c = TestClient(create_app(Settings(db_path=":memory:", **kw)), raise_server_exceptions=False)
    orig = c.request

    def request(method, url, **k):  # uploads are raw bodies: always declare the one accepted content type
        if k.get("content") is not None:
            k["headers"] = {"Content-Type": "application/octet-stream", **(k.get("headers") or {})}
        return orig(method, url, **k)

    c.request = request  # type: ignore[method-assign]
    return c


@pytest.fixture()
def client():
    return make_client()


def upload(c, name="spring_boot.log", **params):
    data = (FIX / name).read_bytes()
    return c.post("/api/imports", params={"filename": name, **params}, content=data,
                  headers={"Content-Type": "application/octet-stream"})


def test_health_is_open_and_reports_auth(client):
    r = client.get("/api/health")
    assert r.status_code == 200 and r.json() == {"status": "ok", "auth_required": False}


def test_upload_then_investigate_flow(client):
    r = upload(client)
    assert r.status_code == 201, r.text
    imp = r.json()
    assert imp["format"] == "java" and imp["event_count"] == 340 and imp["incident_count"] == 4
    assert imp["duplicate"] is False

    detail = client.get(f"/api/imports/{imp['id']}").json()
    assert sum(b["total"] for b in detail["timeline"]) == 340
    assert detail["diagnostics"]["events_parsed"] == 340

    page = client.get(f"/api/imports/{imp['id']}/incidents").json()
    assert page["total"] == 4 and page["items"][0]["count"] == 67 and page["items"][0]["activity"]["state"] == "ongoing"

    top = page["items"][0]
    d = client.get(f"/api/imports/{imp['id']}/incidents/{top['fingerprint']}").json()
    assert d["sample_stack_trace"].startswith("org.springframework.dao") and sum(w["count"] for w in d["windows"]) == 67
    assert d["status"] == "open" and d["notes"] == []

    ev = client.get(f"/api/imports/{imp['id']}/incidents/{top['fingerprint']}/events", params={"limit": 5}).json()
    assert ev["total"] == 67 and len(ev["items"]) == 5
    ts = [e["timestamp"] for e in ev["items"]]
    assert ts == sorted(ts)


def test_reimport_same_file_is_idempotent(client):
    a = upload(client)
    b = upload(client)
    assert a.status_code == 201 and b.status_code == 200
    assert b.json()["id"] == a.json()["id"] and b.json()["duplicate"] is True
    assert client.get("/api/imports").json()["total"] == 1
    # different parse options => a distinct run
    c = upload(client, service="other")
    assert c.status_code == 201 and c.json()["id"] != a.json()["id"]


def test_filters_search_sort_and_pagination(client):
    iid = upload(client).json()["id"]
    base = f"/api/imports/{iid}/incidents"
    assert client.get(base, params={"level": "error"}).json()["total"] == 3
    assert client.get(base, params={"level": "critical"}).json()["total"] == 0
    assert client.get(base, params={"q": "hikari"}).json()["total"] == 1
    assert client.get(base, params={"q": "%"}).json()["total"] == 0  # LIKE wildcard is escaped
    assert client.get(base, params={"ongoing": "true"}).json()["total"] == 1
    assert client.get(base, params={"ongoing": "false"}).json()["total"] == 3
    asc = [i["count"] for i in client.get(base, params={"sort": "count", "order": "asc"}).json()["items"]]
    assert asc == sorted(asc)
    p = client.get(base, params={"limit": 2, "offset": 3}).json()
    assert len(p["items"]) == 1 and p["total"] == 4


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 201}, {"offset": -1}, {"sort": "bogus"}, {"level": "loud"}, {"status": "weird"}])
def test_bad_query_params_rejected(client, params):
    iid = upload(client).json()["id"]
    assert client.get(f"/api/imports/{iid}/incidents", params=params).status_code == 422


def test_sql_injection_strings_are_inert(client):
    iid = upload(client).json()["id"]
    r = client.get(f"/api/imports/{iid}/incidents", params={"q": "' OR 1=1; DROP TABLE groups;--"})
    assert r.status_code == 200 and r.json()["total"] == 0
    assert client.get(f"/api/imports/{iid}/incidents").json()["total"] == 4
    assert client.get(f"/api/imports/{iid}/incidents/x'--").status_code == 404


def test_status_and_notes_persist_across_imports(client):
    a = upload(client).json()
    fp = client.get(f"/api/imports/{a['id']}/incidents").json()["items"][0]["fingerprint"]
    assert client.put(f"/api/incidents/{fp}/status", json={"status": "investigating"}).status_code == 200
    n = client.post(f"/api/incidents/{fp}/notes", json={"body": "  checked pool size  "})
    assert n.status_code == 201 and n.json()["body"] == "checked pool size"
    # same errors in another run (different options) inherit review state
    b = upload(client, service="other").json()
    items = client.get(f"/api/imports/{a['id']}/incidents", params={"status": "investigating"}).json()["items"]
    assert [i["fingerprint"] for i in items] == [fp]
    d = client.get(f"/api/imports/{a['id']}/incidents/{fp}").json()
    assert d["status"] == "investigating" and [x["body"] for x in d["notes"]] == ["checked pool size"]
    assert client.delete(f"/api/incidents/{fp}/notes/{n.json()['id']}").status_code == 204
    assert client.delete(f"/api/incidents/{fp}/notes/{n.json()['id']}").status_code == 404
    assert b["id"] != a["id"]


def test_status_validation_and_unknown_fingerprint(client):
    iid = upload(client).json()["id"]
    fp = client.get(f"/api/imports/{iid}/incidents").json()["items"][0]["fingerprint"]
    assert client.put(f"/api/incidents/{fp}/status", json={"status": "done"}).status_code == 422
    assert client.put(f"/api/incidents/{fp}/status", json={"status": "open", "x": 1}).status_code == 422
    assert client.put("/api/incidents/ffffffffffffffff/status", json={"status": "open"}).status_code == 404
    assert client.post(f"/api/incidents/{fp}/notes", json={"body": "   "}).status_code == 422
    assert client.post(f"/api/incidents/{fp}/notes", json={"body": "x" * 5001}).status_code == 422


def test_upload_rejections(client):
    assert client.post("/api/imports", params={"filename": "a.log"}, content=b"").status_code == 400
    assert client.post("/api/imports", params={"filename": "a.log"}, content=b"just some prose\nnothing here\n").status_code == 415
    assert client.post("/api/imports", params={"filename": "a.bin"}, content=b"\x00\x01\x02" * 100).status_code == 415
    assert client.post("/api/imports", params={"filename": "a.log", "format": "xml"}, content=b"x").status_code == 422
    assert client.post("/api/imports", params={"filename": "a.log", "service": "<script>"}, content=b"x").status_code == 422
    assert client.get("/api/imports").json()["total"] == 0  # failures leave nothing behind


def test_oversize_upload_rejected_and_nothing_stored():
    c = make_client(max_upload_bytes=2000)
    big = (FIX / "spring_boot.log").read_bytes()
    assert c.post("/api/imports", params={"filename": "big.log"}, content=big).status_code == 413
    # chunked body with no content-length is still capped
    r = c.post("/api/imports", params={"filename": "big.log"}, content=(big[i:i + 500] for i in range(0, len(big), 500)))
    assert r.status_code == 413
    assert c.get("/api/imports").json()["total"] == 0


def test_path_in_filename_is_stripped(client):
    r = client.post("/api/imports", params={"filename": "../../etc/passwd.log"},
                    content=(FIX / "python_app.log").read_bytes())
    assert r.status_code == 201 and r.json()["filename"] == "passwd.log"


def test_stored_data_contains_no_secrets(client):
    iid = upload(client, "python_app.log", service="orders-api").json()["id"]
    blob = client.get(f"/api/imports/{iid}/events", params={"limit": 200}).text
    for off in range(0, 500, 200):
        blob += client.get(f"/api/imports/{iid}/events", params={"limit": 200, "offset": off}).text
    for secret in ("hunter2", "s3cr3tPass", "alice@example.com", "abc123SECRETtokenvalue999", "sk-live-1234567890"):
        assert secret not in blob
    assert client.get(f"/api/imports/{iid}").json()["diagnostics"]["redactions"]


def test_exports_csv_formula_injection_and_formats(client):
    payload = (
        '{"level":"error","time":"2026-03-05T10:00:00Z","message":"=HYPERLINK(\\"http://evil\\")"}\n'
        '{"level":"error","time":"2026-03-05T10:00:01Z","message":"+cmd|calc"}\n'
        '{"level":"error","time":"2026-03-05T10:00:02Z","message":"-2+3 failed"}\n'
        '{"level":"error","time":"2026-03-05T10:00:03Z","message":"@SUM(A1)"}\n'
    ).encode()
    iid = client.post("/api/imports", params={"filename": "inj.jsonl"}, content=payload).json()["id"]
    csv_text = client.get(f"/api/imports/{iid}/export", params={"format": "csv"}).text
    for line in csv_text.splitlines()[1:]:
        for cell in __import__("csv").reader([line]).__next__():
            assert not cell.startswith(("=", "+", "-", "@")), cell
    assert "'=HYPERLINK" in csv_text
    j = client.get(f"/api/imports/{iid}/export", params={"format": "json"})
    assert j.status_code == 200 and len(j.json()["incidents"]) == 4
    md = client.get(f"/api/imports/{iid}/export", params={"format": "md"})
    assert md.text.startswith("# StackTriage report") and "attachment" in md.headers["content-disposition"]
    assert client.get(f"/api/imports/{iid}/export", params={"format": "xlsx"}).status_code == 422


def test_xss_payloads_are_stored_as_inert_text_with_json_content_type(client):
    payload = '{"level":"error","time":"2026-03-05T10:00:00Z","message":"<img src=x onerror=alert(1)>"}\n' * 2
    iid = client.post("/api/imports", params={"filename": "x.jsonl"}, content=payload.encode()).json()["id"]
    r = client.get(f"/api/imports/{iid}/events")
    assert r.headers["content-type"].startswith("application/json") and r.headers["x-content-type-options"] == "nosniff"
    assert "<img src=x onerror=alert(1)>" in r.json()["items"][0]["message"]  # data is untouched; UI must render as text
    assert "default-src 'self'" in r.headers["content-security-policy"]


def test_delete_import_cascades(client):
    iid = upload(client).json()["id"]
    assert client.delete(f"/api/imports/{iid}").status_code == 204
    assert client.get(f"/api/imports/{iid}").status_code == 404
    assert client.delete(f"/api/imports/{iid}").status_code == 404
    from sqlalchemy import text
    with client.app.state.engine.connect() as c:
        assert c.execute(text("select count(*) from events")).scalar() == 0
        assert c.execute(text("select count(*) from groups")).scalar() == 0


def test_overview(client):
    assert client.get("/api/overview").json()["latest_import"] is None
    upload(client)
    o = client.get("/api/overview").json()
    assert o["imports"] == 1 and o["events"] == 340 and o["latest_import"]["incident_count"] == 4


# ---------------------------------------------------------------- auth
def test_api_key_required_when_configured():
    c = make_client(api_key=KEY)
    assert c.get("/api/health").status_code == 200
    assert c.get("/api/imports").status_code == 401
    assert c.get("/api/imports", headers={"Authorization": "Bearer wrong" + KEY}).status_code == 401
    assert c.get("/api/imports", headers={"X-API-Key": KEY}).status_code == 200
    assert c.get("/api/imports", headers={"Authorization": f"Bearer {KEY}"}).status_code == 200
    assert c.post("/api/imports", params={"filename": "a.log"}, content=b"x").status_code == 401
    assert c.delete("/api/imports/1").status_code == 401
    assert c.get("/api/imports/1/export").status_code == 401
    assert c.get("/api/overview").status_code == 401


def test_production_requires_key_and_hides_docs():
    with pytest.raises(RuntimeError):
        create_app(Settings(db_path=":memory:", env="production"))
    with pytest.raises(RuntimeError):
        create_app(Settings(db_path=":memory:", api_key="short"))
    c = make_client(env="production", api_key=KEY)
    assert c.get("/api/docs").status_code in (401, 404) and c.get("/api/openapi.json").status_code in (401, 404)


def test_unhandled_error_does_not_leak_details(client, monkeypatch):
    from stacktriage.db import store

    def boom(*a, **k):
        raise RuntimeError("secret internals password=hunter2")
    monkeypatch.setattr(store, "list_incidents", boom)
    iid = upload(client).json()["id"]
    r = client.get(f"/api/imports/{iid}/incidents")
    assert r.status_code == 500 and r.json() == {"detail": "internal server error"}


# ----------------------------------------------------- review-driven hardening
def test_upload_requires_octet_stream_content_type():
    c = TestClient(create_app(Settings(db_path=":memory:")), raise_server_exceptions=False)
    body = (FIX / "python_app.log").read_bytes()
    for ct in ("text/plain", "multipart/form-data", "application/x-www-form-urlencoded", "application/json"):
        r = c.post("/api/imports", params={"filename": "a.log"}, content=body, headers={"Content-Type": ct})
        assert r.status_code == 415, ct
    assert c.post("/api/imports", params={"filename": "a.log"}, content=body).status_code == 415  # none at all


def test_unauthenticated_local_mode_rejects_foreign_host_header():
    c = make_client()
    assert c.get("/api/health", headers={"Host": "evil.example.com"}).status_code == 400
    assert c.get("/api/health", headers={"Host": "localhost:8000"}).status_code == 200


def test_allowed_hosts_configurable_and_open_when_key_set():
    assert make_client(allowed_hosts=("tri.example.com",)).get("/api/health", headers={"Host": "tri.example.com"}).status_code == 200
    assert make_client(api_key=KEY).get("/api/health", headers={"Host": "anything.example"}).status_code == 200


def test_unknown_env_value_is_rejected_not_treated_as_dev():
    with pytest.raises(RuntimeError):
        create_app(Settings(db_path=":memory:", env="prod"))


def test_docs_hidden_when_key_configured():
    c = make_client(api_key=KEY)
    assert c.get("/api/docs").status_code in (401, 404) and c.get("/api/openapi.json").status_code in (401, 404)
    assert make_client().get("/api/openapi.json").status_code == 200


def test_huge_ids_are_client_errors_not_500s(client):
    assert client.get("/api/imports/99999999999999999999").status_code == 422
    assert client.delete("/api/incidents/aaaaaaaaaaaaaaaa/notes/99999999999999999999").status_code == 422


def test_static_serving_traversal_and_special_paths(tmp_path):
    (tmp_path / "index.html").write_text("<html>app</html>")
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "a.js").write_text("console.log(1)")
    (tmp_path.parent / "secret.txt").write_text("TOP SECRET")
    c = make_client(static_dir=str(tmp_path))
    assert "console.log" in c.get("/assets/a.js").text
    assert "app" in c.get("/some/client/route").text
    for p in ("/..%2fsecret.txt", "/%2e%2e/secret.txt", "/assets/..%2f..%2fsecret.txt", "/%5C%5Cattacker%5Cshare%5Cx",
              "/C:/Windows/win.ini", "/%00", "//etc/passwd", "/assets/%2e%2e/%2e%2e/secret.txt"):
        r = c.get(p)
        assert "TOP SECRET" not in r.text and r.status_code in (200, 404), p
    assert c.get("/api/nope").status_code == 404
