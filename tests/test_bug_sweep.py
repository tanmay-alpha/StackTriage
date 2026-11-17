"""Regressions for bugs found by an independent review (each test reproduces the reported input)."""
import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from helpers import run, run_analysis
from stacktriage import normalize_message
from stacktriage.api import Settings, create_app
from stacktriage.cli import main as cli
from stacktriage.db import maintenance as m
from stacktriage.parsers.common import clip_stack


# ---------------------------------------------- long traces keep the root cause
def _java_trace(cause: str) -> str:
    frames = "".join(f"\tat org.springframework.web.servlet.FrameworkServlet.processRequest{i}(FrameworkServlet.java:1000)\n"
                     for i in range(400))
    return ("org.springframework.web.util.NestedServletException: Request processing failed\n" + frames
            + f"Caused by: {cause}\n\tat com.shop.orders.OrderService.place(OrderService.java:42)\n")


def test_long_jsonl_traces_keep_distinct_root_causes():
    lines = [json.dumps({"@timestamp": "2026-03-05T10:00:00Z", "level": "ERROR", "message": "request failed",
                         "stack_trace": _java_trace(cause)})
             for cause in ("java.lang.NullPointerException: order is null", "java.sql.SQLTimeoutException: timed out")]
    assert all(len(json.loads(x)["stack_trace"]) > 32_000 for x in lines)
    sess, res = run_analysis("\n".join(lines) + "\n")
    assert sorted(g.exc_type for g in res.groups) == ["java.lang.NullPointerException", "java.sql.SQLTimeoutException"]
    assert all(g.frame == "OrderService.place:42" or "OrderService" in (g.frame or "") for g in res.groups)
    assert sess.diagnostics.code_counts["STACK_TRUNCATED"] == 2


def test_long_python_tracebacks_keep_distinct_root_causes():
    def rec(exc: str) -> str:
        frames = "".join(f'  File "/srv/app/handlers/h{i}.py", line {i}, in step{i}\n    value = compute({"x" * 700})\n'
                         for i in range(60))
        return f"2026-03-05 10:00:00,000 - app - ERROR - request failed\nTraceback (most recent call last):\n{frames}{exc}\n"
    _, res = run_analysis(rec("KeyError: 'customer_id'") + rec("ZeroDivisionError: division by zero"))
    assert sorted(g.exc_type for g in res.groups) == ["KeyError", "ZeroDivisionError"]


def test_clip_stack_bounds_and_keeps_both_ends():
    text = "HEAD\n" + "\n".join(f"frame {i}" for i in range(5000)) + "\nROOT CAUSE"
    out = clip_stack(text, 1000)
    assert len(out) <= 1000 and out.startswith("HEAD\n") and out.endswith("ROOT CAUSE") and "[truncated]" in out
    assert clip_stack("short", 1000) == "short"


# --------------------------------------------------- nested service objects
def test_ecs_nested_service_object_uses_service_name():
    _, events = run('{"@timestamp":"2026-11-18T10:00:00Z","log.level":"error","message":"boom","service":{"name":"checkout"}}\n')
    assert events[0].service == "checkout"


# ---------------------------------------------------- long service names
def test_long_service_name_fits_the_api_filter():
    c = TestClient(create_app(Settings(db_path=":memory:")))
    svc = "s" * 149
    body = json.dumps({"timestamp": "2026-03-05T10:00:00Z", "level": "error", "service": svc, "message": "boom"}) + "\n"
    iid = c.post("/api/imports", params={"filename": "l.jsonl"}, content=body.encode(),
                 headers={"Content-Type": "application/octet-stream"}).json()["id"]
    stored = c.get(f"/api/imports/{iid}/incidents").json()["items"][0]["service"]
    assert len(stored) == 100 and stored.endswith("[truncated]")
    r = c.get(f"/api/imports/{iid}/incidents", params={"service": stored})
    assert r.status_code == 200 and r.json()["total"] == 1


# --------------------------------------------------- node error codes
def test_node_errors_with_codes_stay_separate_and_keep_frames():
    text = ("2026-03-05T10:00:00.000Z [api] error: TypeError [ERR_INVALID_ARG_TYPE]: The \"path\" argument must be of type string\n"
            "    at loadConfig (/app/src/config.js:12:5)\n"
            "2026-03-05T10:00:01.000Z [api] error: TypeError [ERR_INVALID_URL]: Invalid URL\n"
            "    at parseUser (/app/src/users.js:40:9)\n")
    _, res = run_analysis(text)
    assert len(res.groups) == 2
    assert {g.frame for g in res.groups} == {"config.js:loadConfig:12", "users.js:parseUser:40"}
    assert any("[ERR_INVALID_URL]" in g.title for g in res.groups)


# --------------------------------------------------- HTTP status contexts
@pytest.mark.parametrize(("a", "b"), [
    ("Request failed with status code 404", "Request failed with status code 500"),
    ("got HTTP/1.1 503 Service Unavailable", "got HTTP/1.1 502 Bad Gateway"),
    ("upstream statusCode=429", "upstream statusCode=400"),
])
def test_http_status_codes_are_kept_in_more_contexts(a, b):
    assert normalize_message(a) != normalize_message(b)


def test_http_version_is_not_masked():
    assert normalize_message("got HTTP/1.1 503 Service Unavailable") == "got HTTP/1.1 503 Service Unavailable"


# --------------------------------------------------- restore safety
@pytest.fixture()
def live_db(tmp_path):
    db = tmp_path / "app.db"
    c = TestClient(create_app(Settings(db_path=str(db))))
    c.post("/api/imports", params={"filename": "a.log"}, content=b"2026-03-05 10:00:00,000 - app - ERROR - boom\n",
           headers={"Content-Type": "application/octet-stream"})
    c.app.state.engine.dispose()
    return db


def _imports(db):
    con = sqlite3.connect(db)
    try:
        return con.execute("select count(*) from imports").fetchone()[0]
    finally:
        con.close()


def test_restore_refuses_empty_or_foreign_sqlite_files(live_db, tmp_path, capsys):
    empty = tmp_path / "empty.db"
    empty.write_bytes(b"")
    other = tmp_path / "other.db"
    con = sqlite3.connect(other)
    con.execute("create table t (x)")
    con.commit()
    con.close()
    for bad in (empty, other):
        assert cli(["restore", str(bad), "--db", str(live_db)]) == 1
        assert "not a StackTriage database" in capsys.readouterr().err
    assert _imports(live_db) == 1


def test_check_and_restore_on_a_directory_report_an_error(live_db, tmp_path, capsys):
    d = tmp_path / "adir"
    d.mkdir()
    assert cli(["check", "--db", str(d)]) == 1
    assert cli(["restore", str(d), "--db", str(live_db)]) == 1
    err = capsys.readouterr().err
    assert "Traceback" not in err and "is not a file" in err
    with pytest.raises(m.MaintenanceError):
        m.check(d)
