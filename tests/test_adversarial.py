"""Hostile or pathological input must stay fast and must not crash the parsers."""
import io
import time

import pytest

from stacktriage import analyze, parse

HEADER = "2026-01-01 10:00:00,000 - a - ERROR - "
CASES = {
    "spaces_after_timestamp": "2026-01-01 10:00:00" + " " * 60000 + "x",
    "bracket_spam": "2026-01-01T10:00:00Z " + "[a]" * 20000,
    "level_spam": "2026-01-01 10:00:00 " + "ERROR " * 10000,
    "spring_bracket_spam": "2026-01-01 10:00:00 ERROR 1 --- " + "[x] " * 15000,
    "long_secret_value": "password=" + "a" * 60000,
    "repeated_secret_keys": "password=a " * 6000,
    "email_like_dots": "a." * 30000 + "@",
    "digit_runs": "1 " * 30000,
    "hex_dash_runs": "0123456789abcdef-" * 3000,
    "quote_runs": "'" * 30000,
    "alnum_runs": "a1" * 30000,
    "url_credentials_colons": "http://" + "a:" * 20000 + "@",
    "cookie_blob": "cookie: " + "a" * 60000,
    "clf_garbage": '1.1.1.1 - - [' + "x" * 60000,
}


@pytest.mark.parametrize("name", CASES)
@pytest.mark.parametrize("where", ["message", "continuation"])
def test_pathological_line_is_fast(name, where):
    body = CASES[name]
    text = HEADER + (body if where == "message" else "seed\n" + body) + "\n"
    t = time.perf_counter()
    res = analyze(parse(io.BytesIO(text.encode())))
    assert time.perf_counter() - t < 3
    assert res.total_events >= 1


def test_node_frame_whitespace_is_linear():
    for w in (2000, 30000):
        t = time.perf_counter()
        analyze(parse(io.BytesIO(("2026-01-01T00:00:00Z ERROR boom\n  at" + " " * w + "x\n").encode())))
        assert time.perf_counter() - t < 2


def test_python_frame_and_exception_message_runs_are_linear():
    py = HEADER + "x\nTraceback (most recent call last):\n  File \"a\", line 1, in f" + " " * 30000 + "x\nValueError: boom\n"
    alnum = HEADER + "x\nTraceback (most recent call last):\nValueError: " + "a-" * 15000 + "\n"
    parens = HEADER + "x\n" + ("  at " + "(" * 60000 + "\n") * 200
    for body in (py, alnum, parens):
        t = time.perf_counter()
        analyze(parse(io.BytesIO(body.encode())))
        assert time.perf_counter() - t < 3


def test_huge_traceback_and_deep_json():
    text = HEADER + "x\nTraceback (most recent call last):\n" + '  File "a", line 1, in f\n' * 5000 + "ValueError: x\n"
    t = time.perf_counter()
    res = analyze(parse(io.BytesIO(text.encode())))
    assert time.perf_counter() - t < 3 and res.groups[0].exc_type == "ValueError"
    deep = '{"a":' * 8000 + "1" + "}" * 8000 + "\n" + '{"message":"m","level":"error"}\n' * 3
    assert analyze(parse(io.BytesIO(deep.encode()))).total_events == 3
