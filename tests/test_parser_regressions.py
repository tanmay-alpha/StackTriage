"""Regression coverage for malformed records and multiline source fidelity."""
import io

import pytest

from helpers import run
from stacktriage import Level, Limits, parse
from stacktriage.fingerprint import extract_root
from stacktriage.parsers.common import normalize_level


def test_csv_preserves_blank_lines_inside_quoted_messages_and_physical_line_numbers():
    session, events = run('\n timestamp,level,message\n2026-01-01T00:00:00Z,ERROR,"first\n\nlast"\n\n2026-01-01T00:00:01Z,INFO,next\n')
    assert [event.message for event in events] == ["first\n\nlast", "next"]
    assert [event.line_no for event in events] == [3, 7]
    assert session.diagnostics.lines_read == 7


def test_csv_column_limit_retains_records_and_reports_omitted_columns():
    session, events = run('timestamp,level,message,extra\n2026-01-01T00:00:00Z,ERROR,boom,ignored\n', limits=Limits(max_csv_columns=3))
    assert len(events) == 1 and events[0].message == "boom"
    assert session.diagnostics.code_counts["CSV_COLUMNS_CAPPED"] == 1
    assert "BAD_ROW_WIDTH" not in session.diagnostics.code_counts


def test_unterminated_csv_quote_is_reported_not_accepted_as_a_record():
    session, events = run('timestamp,level,message\n2026-01-01T00:00:00Z,ERROR,"unfinished\n')
    assert events == []
    assert session.diagnostics.code_counts["CSV_ERROR"] == 1


def test_huge_epoch_does_not_abort_following_records():
    session, events = run('{"timestamp":' + '9' * 400 + ',"level":"error","message":"bad time"}\n{"timestamp":"2026-01-01T00:00:00Z","level":"info","message":"ok"}\n')
    assert len(events) == 2 and events[0].timestamp is None
    assert events[1].message == "ok"
    assert session.diagnostics.code_counts["BAD_TIMESTAMP"] == 1


def test_malformed_nested_error_type_does_not_abort_import():
    _, events = run('{"level":"error","err":{"type":{"unexpected":1},"message":"bad metadata"}}\n{"level":"info","message":"ok"}\n')
    assert len(events) == 2 and "bad metadata" in events[0].message
    assert events[1].message == "ok"


def test_exception_object_extracts_actual_stack_not_serialized_metadata():
    _, events = run('{"level":"error","exception":{"type":"ValueError","message":"bad","stack":"Traceback (most recent call last):\\n  File \\\"app.py\\\", line 2, in f\\nValueError: bad"}}\n')
    assert events[0].stack_trace.startswith("Traceback (")
    root = extract_root(events[0].message, events[0].stack_trace)
    assert root.exc_type == "ValueError" and root.frame == "app.py:f:2"


@pytest.mark.parametrize("value", [20.9, 30.5, 50.1])
def test_fractional_numeric_levels_are_not_rounded_to_valid_severities(value):
    assert normalize_level(value) is Level.UNKNOWN
    assert normalize_level(value, pino_scale=True) is Level.UNKNOWN


def test_discarding_a_long_line_still_enforces_byte_limit():
    payload = b'ERROR:root:' + b'x' * 10000 + b'\nERROR:root:after limit\n'
    session = parse(io.BytesIO(payload), format="python", limits=Limits(max_line_chars=30, max_bytes=300))
    events = list(session)
    assert session.diagnostics.truncated
    assert session.diagnostics.code_counts["FILE_TOO_LARGE"] == 1
    assert all(event.message != "after limit" for event in events)
    assert session.diagnostics.bytes_read <= 301


def test_character_stack_limit_reports_truncation():
    session, events = run('ERROR:root:failed\nTraceback (most recent call last):\n' + '  frame data\n' * 10 + 'ValueError: bad\n', limits=Limits(max_stack_chars=60))
    assert "truncated" in events[0].stack_trace
    assert session.diagnostics.code_counts["STACK_TRUNCATED"] == 1


def test_headerless_chained_python_traceback_is_one_event():
    text = ('Traceback (most recent call last):\n  File "first.py", line 1, in f\nValueError: inner\n\n'
            'The above exception was the direct cause of the following exception:\n\n'
            'Traceback (most recent call last):\n  File "outer.py", line 2, in g\nRuntimeError: outer\n')
    _, events = run(text)
    assert len(events) == 1
    assert events[0].message == "RuntimeError: outer"
    assert "ValueError: inner" in events[0].stack_trace
