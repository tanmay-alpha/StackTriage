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
