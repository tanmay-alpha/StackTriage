import io

import pytest

from stacktriage import parse, redact
from stacktriage.redaction import is_sensitive_key


@pytest.mark.parametrize("text,secret", [
    ("Authorization: Bearer abc123SECRETtokenvalue999", "abc123SECRET"),
    ('{"password":"hunter2","user":"x"}', "hunter2"),
    ("password=hunter2&next=/home", "hunter2"),
    ("connecting postgresql://orders:s3cr3tPass@10.0.3.12:5432/db", "s3cr3tPass"),
    ("key AKIAIOSFODNN7EXAMPLE used", "AKIAIOSFODNN7EXAMPLE"),
    ("token eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcdefghijk", "eyJhbGci"),
    ("contact alice@example.com now", "alice@example.com"),
    ("card 4111 1111 1111 1111 declined", "4111 1111 1111 1111"),
    ("ghp_" + "abcdefghijklmnopqrstuvwxyz0123456789", "ghp_abcdef"),  # token built from parts so secret scanners ignore the fake,
    ("Cookie: session=abc; theme=dark", "session=abc"),
    ("-----BEGIN RSA PRIVATE KEY-----\nMIIEow\n-----END RSA PRIVATE KEY-----", "MIIEow"),
    ("client_secret: 'shh-very-secret'", "shh-very-secret"),
    ("{'Authorization': 'Bearer 7f3a9c1e5d'}", "7f3a9c1e5d"),
    ('{"cookie":"sid=abc123; csrf=def456"}', "sid=abc123"),
    ('login failed password="hunter 2"', "hunter 2"),
    ('{"password":"a,b;c"}', "a,b;c"),
    ("'secret': 'p;ss w0rd'", "p;ss w0rd"),
    ("redis://:hunter2@redis:6379/0", "hunter2"),
    ("Authorization: Negotiate YIIBabc123xyz", "YIIBabc123xyz"),
    ("Authorization: NTLM TlRMTVNTUAAB", "TlRMTVNTUAAB"),
    ("key sk_live_" + "4eC39HqLyjWDarjtT1zdp7dc", "4eC39HqLyjWDarjtT1zdp7dc"),
    ("token glpat-" + "abcdefghijklmnopqrstu", "abcdefghijklmnopqrstu"),
    ("AccountKey=Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw==", "Eby8vdM02xNOcqFlqUwJPLlm"),
    ("post https://hooks.slack.com/services/T0ABCDEFG/B0ABCDEFG/Xyz123Secret", "Xyz123Secret"),
    ("passphrase=correcthorsebatterystaple", "correcthorse"),
    ("userPassword=Sup3rS3cret", "Sup3rS3cret"),
    ("db_password: opensesame", "opensesame"),
    ("SECRET_KEY='django-insecure-abc'", "django-insecure-abc"),
    ("aws_secret_key: wJalrXUtnFEMI", "wJalrXUtnFEMI"),
    ("postgres://app:p@ss!word@db:5432/x", "ss!word"),
])
def test_secrets_removed(text, secret):
    out = redact(text)
    assert secret not in out
    assert "REDACTED" in out


def test_non_secrets_untouched():
    msg = "Order 8841 failed: user not found at 10.0.3.12:5432 after 3000ms"
    assert redact(msg) == msg


@pytest.mark.parametrize("text", [
    "at hashPassword (/srv/lib/password.js:22:11)",
    "Invalid token: expired",
    "max_tokens=100 used",
    "bypass=true compass=north",
    "token.js:17 failed",
    'File "/srv/auth/secret.py", line 9, in load',
    "at com.shop.auth.Credentials.load(Credentials.java:12)",
])
def test_debugging_text_not_over_redacted(text):
    assert redact(text) == text


def test_non_luhn_digit_runs_kept():
    assert redact("trace id 1234 5678 9012 3456") == "trace id 1234 5678 9012 3456"


def test_counts_reported():
    counts: dict[str, int] = {}
    redact("a@b.co and c@d.io password=x", counts)
    assert counts["email"] == 2 and counts["secret_kv"] == 1


def test_no_catastrophic_backtracking():
    import time

    t = time.perf_counter()
    redact("password" + "=" * 20000 + "x" * 20000)
    redact("a" * 60000 + "@")
    assert time.perf_counter() - t < 2


@pytest.mark.parametrize("quote", ["", "'", '"'])
@pytest.mark.parametrize("size", [501, 2000])
def test_long_secret_is_removed_completely(quote, size):
    secret = "Q" * size + "SENSITIVE_TAIL"
    out = redact(f"password={quote}{secret}{quote} next=ok")
    assert "SENSITIVE_TAIL" not in out
    assert "Q" not in out
    assert "[REDACTED:secret]" in out
    assert "next=ok" in out


@pytest.mark.parametrize("key", [
    "processName", "className", "classname", "businessName", "isSnapshot", "prompt_tokens", "max_tokens",
    "tokenizer", "pass_rate", "tests_pass", "allowPassThrough", "firstPass",
])
def test_ordinary_structured_keys_not_sensitive(key):
    assert not is_sensitive_key(key)


@pytest.mark.parametrize("key", [
    "password", "passwd", "db_password", "DB_PASS", "pass", "dbPass", "userPassword", "secret", "client_secret",
    "secretKey", "SECRET_KEY", "token", "access_token", "accessToken", "refresh_token", "api_key", "apiKey",
    "x-api-key", "authorization", "cookie", "set-cookie", "credentials", "private_key", "access_key",
    "session_id", "sessionid", "account_key", "ssn", "user_ssn", "pwd", "PGPASSWORD",
])
def test_sensitive_structured_keys(key):
    assert is_sensitive_key(key)


def test_structured_fields_and_metrics_kept():
    line = ('{"asctime":"2026-11-18T10:00:00Z","levelname":"ERROR","message":"x","processName":"MainProcess",'
            '"className":"OrderService","prompt_tokens":512,"pass_rate":0.97,"password":"hunter2"}\n')
    [ev] = list(parse(io.BytesIO(line.encode())))
    assert ev.fields["processName"] == "MainProcess"
    assert ev.fields["className"] == "OrderService"
    assert ev.metrics == {"prompt_tokens": 512.0, "pass_rate": 0.97}
    assert "hunter2" not in repr(ev)


@pytest.mark.parametrize("text,secret", [
    ("PGPASSWORD=s3cr3t psql -h db", "s3cr3t"),
    ("Cookie: session=abc123def456; theme=dark", "abc123def456"),
    ("Set-Cookie: sid=xyz789abc; Path=/; HttpOnly", "xyz789abc"),
    ("Authorization: Bearer abc123SECRETtokenvalue999", "abc123SECRETtokenvalue999"),
    ("Authorization: Basic dXNlcjpwYXNz", "dXNlcjpwYXNz"),
    ("authorization=Token 9f8e7d6c5b4a3f2e", "9f8e7d6c5b4a3f2e"),
    ("password: hunter2", "hunter2"),
    ("pass=S3cret!", "S3cret!"),
])
def test_regression_secrets_still_removed(text, secret):
    out = redact(text)
    assert secret not in out
    assert "REDACTED" in out


@pytest.mark.parametrize("text", [
    "bypass=1",
    "compass=north",
    "password.js:22",
    "Invalid cookie: expected 2 parts, got 3 (request id 1234 for /checkout)",
    "authorization: denied for user 42",
    "tests pass: 15 fail: 2",
])
def test_regression_prose_kept(text):
    assert redact(text) == text
