import io

import pytest

from helpers import run, run_analysis
from stacktriage import Level, Limits, UnsupportedFormatError, parse


def test_python_multiline_traceback_attached_to_header():
    text = (
        "2026-03-05 10:00:01,123 - app - ERROR - Failed\n"
        "Traceback (most recent call last):\n"
        '  File "/a/b.py", line 3, in f\n    x()\n'
        "ValueError: bad\n"
        "2026-03-05 10:00:02,000 - app - INFO - ok\n"
    )
    sess, ev = run(text, default_service="svc")
    assert sess.format == "python" and len(ev) == 2
    assert ev[0].level is Level.ERROR and ev[0].message == "Failed"
    assert ev[0].stack_trace.splitlines()[-1] == "ValueError: bad"
    assert ev[0].service == "svc" and ev[1].stack_trace is None
    assert ev[0].timestamp.isoformat() == "2026-03-05T10:00:01.123000+00:00"


def test_python_basicconfig_without_timestamps_is_diagnosed():
    sess, ev = run("ERROR:root:disk full\nINFO:root:ok\nWARNING:app.x:careful\n")
    assert [e.level for e in ev] == [Level.ERROR, Level.INFO, Level.WARNING]
    assert all(e.timestamp is None for e in ev)
    assert sess.diagnostics.code_counts["MISSING_TIMESTAMP"] == 3
    assert sess.diagnostics.events_without_timestamp == 3


def test_headerless_python_traceback_becomes_event():
    text = 'Traceback (most recent call last):\n  File "x.py", line 1, in <module>\n    1/0\nZeroDivisionError: division by zero\n'
    sess, ev = run(text)
    assert len(ev) == 1 and ev[0].level is Level.ERROR
    assert ev[0].message == "ZeroDivisionError: division by zero"


def test_spring_boot_app_name_and_thread():
    text = "2026-03-05T10:00:01.123+01:00  ERROR 12 --- [myapp,abc,def] [nio-8080-exec-1] c.e.Foo : boom\n\tat x.Y.z(Y.java:1)\n"
    sess, ev = run(text)
    assert sess.format == "java"
    assert ev[0].service == "myapp" and ev[0].logger == "c.e.Foo" and ev[0].fields["thread"] == "nio-8080-exec-1"
    assert ev[0].timestamp.isoformat() == "2026-03-05T09:00:01.123000+00:00"  # offset converted to UTC


def test_logback_pattern():
    sess, ev = run("2026-03-05 10:00:01,123 [main] WARN  c.e.Foo - careful\n")
    assert sess.format == "java" and ev[0].level is Level.WARNING and ev[0].logger == "c.e.Foo"


def test_ansi_colors_and_bom_stripped():
    text = "\ufeff\x1b[31m2026-03-05T10:00:01.123Z\x1b[0m [api] \x1b[31merror\x1b[0m: boom\n"
    sess, ev = run(text)
    assert ev[0].level is Level.ERROR and ev[0].message == "boom"


def test_jsonl_levels_epoch_and_nested_error():
    text = (
        '{"level":50,"time":1772704801123,"pid":1,"hostname":"h","name":"pay","msg":"x","err":{"type":"Error","message":"m","stack":"Error: m\\n    at f (/a.js:1:2)"}}\n'
        '{"timestamp":"2026-03-05T10:00:02Z","level":"warn","message":"slow","latency_ms":12.5,"token":"abc"}\n'
    )
    sess, ev = run(text)
    assert sess.format == "jsonl"
    assert ev[0].level is Level.ERROR and ev[0].service == "pay" and ev[0].stack_trace.startswith("Error: m")
    assert ev[0].timestamp.year == 2026
    assert ev[1].level is Level.WARNING and ev[1].metrics == {"latency_ms": 12.5}
    assert "token" not in ev[1].metrics


def test_jsonl_invalid_lines_reported_not_fatal():
    text = '{"level":"info","message":"a","time":"2026-01-01T00:00:00Z"}\n{broken\n[1,2]\n{"level":"info","message":"b","time":"2026-01-01T00:00:01Z"}\n{"x":1}\n'
    sess, ev = run(text)
    assert len(ev) == 3
    codes = sess.diagnostics.code_counts
    assert codes["INVALID_JSON"] == 1 and codes["NOT_AN_OBJECT"] == 1
    assert codes["MISSING_TIMESTAMP"] == 1 and codes["MISSING_MESSAGE"] == 1


def test_deeply_nested_json_does_not_crash():
    sess, ev = run('{"a":' * 5000 + "1" + "}" * 5000 + "\n" + '{"message":"ok","level":"info"}\n' * 3)
    assert len(ev) == 3 and sess.diagnostics.code_counts["INVALID_JSON"] == 1


def test_json_sensitive_field_values_redacted():
    sess, ev = run('{"message":"m","level":"info","password":"p4ss","note":"mail bob@x.io","api_key":"zzz"}\n')
    assert ev[0].fields["api_key"] == "[REDACTED:secret]"
    assert "p4ss" not in str(ev[0]) and "bob@x.io" not in str(ev[0])


def test_csv_columns_metrics_and_bad_rows():
    text = (
        "timestamp,host,level,message,cpu_pct\n"
        '2026-03-05T10:00:00Z,a,ERROR,"timeout, retrying",91.5\n'
        "2026-03-05T10:00:01Z,a,INFO,short\n"
        "bad-ts,a,INFO,ok,3\n"
        "\n"
        "2026-03-05T10:00:02Z,a,LOUD,ok,3\n"
    )
    sess, ev = run(text, filename="t.csv")
    assert sess.format == "csv" and len(ev) == 3
    assert ev[0].message == "timeout, retrying" and ev[0].metrics == {"cpu_pct": 91.5} and ev[0].fields == {"host": "a"}
    c = sess.diagnostics.code_counts
    assert c["BAD_ROW_WIDTH"] == 1 and c["BAD_TIMESTAMP"] == 1 and c["UNKNOWN_LEVEL"] == 1


def test_csv_nan_inf_not_metrics():
    sess, ev = run("timestamp,level,message,v\n2026-03-05T10:00:00Z,INFO,m,nan\n2026-03-05T10:00:01Z,INFO,m,inf\n")
    assert all(e.metrics == {} for e in ev)


@pytest.mark.parametrize("payload", [b"", b"\n\n  \n", b"hello world\nnothing parseable here\n", b"\x00\x01\x02binary\x00", "A\u0000B".encode("utf-16")])
def test_unsupported_input_rejected(payload):
    with pytest.raises(UnsupportedFormatError):
        parse(io.BytesIO(payload))


def test_unknown_explicit_format_rejected():
    with pytest.raises(ValueError):
        parse(io.BytesIO(b"x"), format="xml")


def test_orphan_lines_before_first_header_reported():
    sess, ev = run("garbage line\nmore garbage\n2026-03-05 10:00:01,123 - a - INFO - hi\n")
    assert len(ev) == 1 and sess.diagnostics.code_counts["ORPHAN_LINE"] == 2


def test_naive_timestamp_is_flagged_utc():
    sess, ev = run("2026-03-05 10:00:01,123 - a - INFO - hi\n")
    assert sess.diagnostics.code_counts["TIMEZONE_ASSUMED_UTC"] == 1 and ev[0].timestamp.utcoffset().total_seconds() == 0


def test_secrets_never_reach_events_and_are_counted():
    text = "2026-03-05 10:00:01,123 - a - ERROR - login failed password=hunter2 for bob@example.com\nTraceback (most recent call last):\n  File \"a.py\", line 1, in f\nRuntimeError: Authorization: Bearer abcdefghijklmnop\n"
    sess, ev = run(text)
    blob = ev[0].message + ev[0].stack_trace
    assert "hunter2" not in blob and "bob@example.com" not in blob and "abcdefghijklmnop" not in blob
    assert sess.diagnostics.redactions["secret_kv"] >= 1 and sess.diagnostics.redactions["email"] == 1


def test_event_ids_stable_and_unique_for_duplicates():
    text = "2026-03-05 10:00:01,123 - a - INFO - same\n" * 3
    _, a = run(text)
    _, b = run(text)
    assert [e.event_id for e in a] == [e.event_id for e in b]
    assert len({e.event_id for e in a}) == 3


def test_event_ids_survive_overlapping_export_with_shifted_lines():
    lines = [f"2026-03-05 10:00:{i:02d},000 - a - INFO - msg {i}\n" for i in range(10)]
    _, full = run("".join(lines))
    _, tail = run("junk header\n" + "".join(lines[4:]))
    assert {e.event_id for e in tail} == {e.event_id for e in full[4:]}


def test_stack_trace_cap_keeps_head_and_tail():
    lim = Limits(max_stack_lines=10)
    body = "".join(f"  frame {i}\n" for i in range(50)) + "ValueError: the real error\n"
    sess, ev = run("2026-03-05 10:00:01,123 - a - ERROR - x\nTraceback (most recent call last):\n" + body, limits=lim)
    lines = ev[0].stack_trace.splitlines()
    assert len(lines) <= 11 and "omitted" in ev[0].stack_trace
    assert lines[-1] == "ValueError: the real error" and lines[0].startswith("Traceback")
    assert sess.diagnostics.code_counts["STACK_TRUNCATED"] == 1


def test_java_caused_by_in_omitted_middle_is_kept():
    lim = Limits(max_stack_lines=10)
    text = ("2026-03-05 10:00:01.123 ERROR 1 --- [t] c.e.Foo : boom\n" + "java.lang.RuntimeException: wrapper\n"
            + "".join(f"\tat a.B.m{i}(B.java:{i})\n" for i in range(30)) + "Caused by: java.io.IOException: mid\n"
            + "".join(f"\tat a.C.m{i}(C.java:{i})\n" for i in range(30)) + "Caused by: java.lang.IllegalStateException: root\n\tat a.D.go(D.java:7)\n")
    _, ev = run(text, limits=lim)
    assert "IllegalStateException: root" in ev[0].stack_trace


def test_event_limit_flags_truncation():
    text = "".join(f"2026-03-05 10:00:{i:02d},000 - a - INFO - m\n" for i in range(50))
    sess, ev = run(text, limits=Limits(max_events=10))
    assert len(ev) == 10 and sess.diagnostics.truncated and "event limit" in sess.diagnostics.truncated_reason


def test_byte_limit_flags_truncation():
    text = "".join(f"2026-03-05 10:{i // 60:02d}:{i % 60:02d},000 - a - INFO - m\n" for i in range(100))
    sess, ev = run(text, limits=Limits(max_bytes=500))
    assert sess.diagnostics.truncated and 0 < len(ev) < 100


def test_explicit_format_override():
    sess, ev = run("2026-03-05 10:00:01,123 - a - INFO - hi\n", format="python")
    assert sess.format == "python"


def test_nothing_is_executed():
    # content that looks like code/templates is data only
    text = "2026-03-05 10:00:01,123 - a - ERROR - __import__('os').system('echo pwned') {{7*7}} ${jndi:ldap://x}\n"
    sess, res = run_analysis(text)
    assert res.groups[0].sample_message.startswith("__import__")
