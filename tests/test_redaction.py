import pytest

from stacktriage import redact


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
])
def test_secrets_removed(text, secret):
    out = redact(text)
    assert secret not in out
    assert "REDACTED" in out


def test_non_secrets_untouched():
    msg = "Order 8841 failed: user not found at 10.0.3.12:5432 after 3000ms"
    assert redact(msg) == msg


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
