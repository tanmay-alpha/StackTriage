from datetime import UTC, datetime

from stacktriage import Level, LogEvent, fingerprint, normalize_message


def ev(msg, trace=None, service="svc", level=Level.ERROR):
    return LogEvent("", 1, "python", datetime(2026, 1, 1, tzinfo=UTC), level, service, None, msg, trace)


def fp(msg, trace=None, **kw):
    return fingerprint(ev(msg, trace, **kw)).key


def test_variable_ids_merge():
    a = "Failed to process order 8841 for customer 5521 (req 7f3a9c1e22b4aa10)"
    b = "Failed to process order 9120 for customer 101 (req 00ffaa11bb22cc33)"
    assert fp(a) == fp(b)


def test_uuid_ip_timestamp_hex_merge():
    a = "job 3f2b8c1a-0d4e-4a7b-9c1d-2e5f6a7b8c9d from 10.1.2.3 at 2026-03-05T10:00:01Z addr 0x7ffd1234"
    b = "job aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee from 192.168.0.7 at 2026-03-06T11:11:11Z addr 0x7ffdabcd"
    assert fp(a) == fp(b)


def test_different_words_do_not_merge():
    assert fp("Connection refused to database") != fp("Connection timed out to database")
    assert fp("Connection refused to database") != fp("Connection refused to cache")


def test_http_status_codes_kept_apart():
    assert fp("HTTP 500 GET /a/1") != fp("HTTP 404 GET /a/1")


def test_ports_kept_ephemeral_masked():
    assert fp("connect ECONNREFUSED 127.0.0.1:6379") != fp("connect ECONNREFUSED 127.0.0.1:5432")
    assert fp("reset by 10.0.0.1:51234") == fp("reset by 10.0.0.1:60001")


def test_service_is_part_of_identity():
    assert fp("boom", service="a") != fp("boom", service="b")


def test_level_is_not_part_of_identity():
    assert fp("cache miss storm", level=Level.WARNING) == fp("cache miss storm", level=Level.ERROR)


def test_units_do_not_look_like_ids():
    assert normalize_message("timed out after 950ms") == normalize_message("timed out after 30000ms")


PY = (
    "Traceback (most recent call last):\n"
    '  File "/srv/app/api.py", line 10, in handler\n    parse(x)\n'
    '  File "/srv/app/orders.py", line {line}, in parse_qty\n    return int(raw)\n'
    "ValueError: invalid literal for int() with base 10: '{bad}'"
)


def test_same_traceback_different_values_merge():
    a = fp("Failed order 1", PY.format(line=47, bad="x12"))
    b = fp("Failed order 2 totally different header", PY.format(line=47, bad=""))
    assert a == b


def test_same_exception_type_different_location_separate():
    assert fp("x", PY.format(line=47, bad="a")) != fp("x", PY.format(line=99, bad="a"))


def test_same_location_different_exception_separate():
    t1 = PY.format(line=47, bad="a")
    t2 = t1.replace("ValueError: invalid literal for int() with base 10: 'a'", "TypeError: expected str, got NoneType")
    assert fp("x", t1) != fp("x", t2)


def test_python_library_frames_skipped_for_location():
    base = (
        "Traceback (most recent call last):\n"
        '  File "/srv/app/db.py", line 33, in get_session\n    q()\n'
        '  File "/srv/venv/lib/python3.11/site-packages/psycopg2/__init__.py", line {n}, in connect\n    c()\n'
        "psycopg2.OperationalError: refused"
    )
    # the library line number changes with a library upgrade; the app frame is what identifies the bug
    assert fp("x", base.format(n=122)) == fp("x", base.format(n=130))


def test_java_root_cause_used_not_wrapper():
    wrapped = (
        "org.springframework.web.NestedServletException: Request processing failed; nested exception is java.lang.IllegalStateException: boom\n"
        "\tat org.springframework.web.servlet.FrameworkServlet.service(FrameworkServlet.java:883)\n"
        "Caused by: java.lang.IllegalStateException: boom\n"
        "\tat com.shop.A.run(A.java:10)\n"
    )
    f = fingerprint(ev("err", wrapped))
    assert f.exc_type == "java.lang.IllegalStateException"
    assert f.frame == "com.shop.A.run:10"


def test_node_trace_skips_node_modules_frames():
    t = (
        "TypeError: Cannot read properties of undefined (reading 'id')\n"
        "    at next (/app/node_modules/express/lib/router/route.js:144:13)\n"
        "    at getUser (/app/routes/users.js:42:19)\n"
    )
    f = fingerprint(ev("x", t))
    assert f.exc_type == "TypeError" and f.frame == "users.js:getUser:42"


def test_quoted_property_names_separate_when_message_only():
    assert fp("Cannot read properties of undefined (reading 'id')") != fp(
        "Cannot read properties of undefined (reading 'email')")


def test_known_limitation_unquoted_free_text_values_split():
    # Documented limitation: variable *unquoted words* are not masked.
    assert fp("Unknown currency code XQZ") != fp("Unknown currency code ZZZ")


def test_deterministic_and_stable_key_format():
    k = fp("Order 5 failed")
    assert k == fp("Order 5 failed") and len(k) == 16 and int(k, 16) >= 0
