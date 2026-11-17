"""Credential and personal-data redaction.

Applied by parsers before any text is stored in an event, so unredacted values
never reach the database, API responses, or exports. Pattern-based redaction is
best effort: it catches common credential shapes, not arbitrary secrets.
Every pattern is linear or length-bounded (see tests/test_adversarial.py).
"""
from __future__ import annotations

import re
from collections.abc import Callable

_PEM = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)", re.S
)

# (name, pattern, replacement). Order matters: specific shapes before generic ones.
_RULES: list[tuple[str, re.Pattern[str], str]] = [
    ("private_key", _PEM, "[REDACTED:private_key]"),
    (
        "jwt",
        re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]*"),
        "[REDACTED:jwt]",
    ),
    ("aws_key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), "[REDACTED:aws_key]"),
    (
        "api_token",
        re.compile(
            r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|glpat-[A-Za-z0-9_-]{20,}"
            r"|xox[abprs]-[A-Za-z0-9-]{10,}|(?:sk|rk|pk)_(?:live|test)_[A-Za-z0-9]{16,}"
            r"|sk-[A-Za-z0-9_-]{20,}|AIza[0-9A-Za-z_-]{30,}|SG\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,})"
        ),
        "[REDACTED:api_token]",
    ),
    (
        "webhook_url",
        re.compile(r"https://hooks\.slack\.com/(?:services|workflows)/[A-Za-z0-9/_-]{10,}"
                   r"|https://discord(?:app)?\.com/api/webhooks/[0-9]{5,}/[A-Za-z0-9_-]{10,}"),
        "[REDACTED:webhook_url]",
    ),
    ("bearer_token", re.compile(r"(?i)\bbearer[ \t]+[A-Za-z0-9._~+/=-]{12,}"), "Bearer [REDACTED:bearer_token]"),
    (
        "auth_header",
        # Authorization: <scheme> <credentials>; the scheme word is optional and arbitrary
        # (Bearer, Basic, Negotiate, NTLM, ...). Quoted names ("Authorization": "...") allowed.
        # The value must look like a credential (>= 8 chars after a scheme, or a long opaque token
        # containing a digit/uppercase/base64 char) so prose such as "authorization: denied for user" survives.
        re.compile(
            r"(?i)\b(authorization|proxy-authorization)([\"']?\s*[:=]\s*[\"']?)"
            r"(?:[A-Za-z][\w-]{0,30}[ \t]+[^\s\"',;]{8,}"
            r"|(?=[^\s\"',;]{16})(?=(?-i:[^\s\"',;]{0,200}[0-9A-Z+/=]))[^\s\"',;]+)"
        ),
        r"\1\2[REDACTED:auth_header]",
    ),
    (
        "cookie",
        # The value must start with a name=value pair (session=abc; theme=dark) so prose such as
        # "Invalid cookie: expected 2 parts" is kept. The rest of the line/quoted string is redacted.
        re.compile(
            r"(?i)\b(set-cookie|cookie)([\"']?\s*[:=]\s*)"
            r"(?:\"[\w!#$%&*+.^`|~-]+=[^\"\r\n]*\"|'[\w!#$%&*+.^`|~-]+=[^'\r\n]*'|[\w!#$%&*+.^`|~-]+=[^\r\n]*)"
        ),
        r"\1\2[REDACTED:cookie]",
    ),
    (
        "url_credentials",
        # scheme://user:pass@host and scheme://:pass@host (redis); user may be empty. An unencoded
        # '@' inside the password is consumed up to the last '@' of the authority.
        re.compile(r"(?i)\b([a-z][a-z0-9+.-]{1,20}://)[^\s/:@\"']{0,100}:[^\s/\"']{1,200}@"),
        r"\1[REDACTED:credentials]@",
    ),
    (
        "email",
        re.compile(r"\b[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,255}\.[A-Za-z]{2,24}\b"),
        "[REDACTED:email]",
    ),
]

# Keywords that must stand alone (not preceded by a letter/digit): `bypass=1`, `compass=north` are not keys.
_SHORT_KEYWORDS = (
    r"pass|pwd|api[_-]?key|apikey|access[_-]?key|account[_-]?key"
    r"|private[_-]?key|client[_-]?secret|credentials?|session[_-]?id|sessionid"
)
# Long keywords may carry a letter prefix, as in env names: PGPASSWORD, DBSECRET, CSRFTOKEN.
_LONG_KEYWORDS = r"password|passwd|passphrase|secret|token"
_VALUE = (
    r"(?P<v>\"(?:[^\"\\\r\n]|\\.)*\"|'(?:[^'\\\r\n]|\\.)*'|[^\s\"',;&}\])]+)"
)
# key=value / "key": "value". The keyword must END the key (so `password.js:22` is not a key), optionally
# followed by a `key` suffix (secret_key, SECRET_KEY, secretKey), and be delimited from preceding text.
_KV = re.compile(
    rf"(?i)(?P<k>[\"']?(?:[\w.-]{{0,30}}[_.-])?(?<![A-Za-z0-9])"
    rf"(?:(?P<kw>{_SHORT_KEYWORDS})|[A-Za-z]{{0,20}}?(?P<kwl>{_LONG_KEYWORDS}))"
    rf"s?(?:[_.-]?key)?[\"']?\s*[:=]\s*){_VALUE}"
)
# camelCase keys: userPassword, dbSecret, accessToken
_KV_CAMEL = re.compile(
    r"(?P<k>[\"']?[a-z][A-Za-z0-9]{0,30}(?P<kw>Password|Passwd|Secret|Token|ApiKey|AccessKey|PrivateKey)[\"']?\s*[:=]\s*)"
    + _VALUE
)
_CARD = re.compile(r"\b(?:\d[ -]?){12,18}\d\b")
_LOWER_WORD = re.compile(r"[a-z]+")

# Structured (JSON/CSV) key names whose values are always redacted. Keys are split into parts at
# `_` `.` `-` / whitespace and camelCase humps, so keywords only match on part boundaries:
# processName, className, isSnapshot, tokenizer, prompt_tokens (a count) are ordinary fields.
_KEY_PART = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|[0-9]+")
_SENSITIVE_PARTS = frozenset({
    "password", "passwords", "passwd", "passphrase", "pwd", "secret", "secrets", "token", "authorization",
    "cookie", "cookies", "credential", "credentials", "ssn", "apikey", "accesskey", "privatekey",
    "accountkey", "sessionid",
})
_SENSITIVE_PAIRS = frozenset({
    ("api", "key"), ("access", "key"), ("private", "key"), ("account", "key"), ("session", "id"),
})
# A single part ending in one of these is sensitive too: PGPASSWORD, clientsecret, csrftoken, jsessionid.
_SENSITIVE_SUFFIXES = (
    "password", "passwd", "passphrase", "secret", "token", "authorization", "cookie",
    "apikey", "accesskey", "privatekey", "accountkey", "sessionid",
)
# `pass` is a credential only as the last part (pass, db_pass, DB_PASS, dbPass); these qualifiers make
# it a test/compile outcome or a counter instead (tests_pass, firstPass).
_NON_SECRET_PASS_QUALIFIERS = frozenset({
    "test", "tests", "check", "checks", "first", "second", "third", "last", "final", "next", "prev",
    "previous", "single", "multi", "one", "two", "each", "every", "num", "total", "max", "min", "build",
    "compile", "render", "lint", "by",
})


def _luhn_ok(digits: str) -> bool:
    total, alt = 0, False
    for ch in reversed(digits):
        d = ord(ch) - 48
        if alt:
            d *= 2
            if d > 9:
                d -= 9
        total += d
        alt = not alt
    return total % 10 == 0


def is_sensitive_key(key: str) -> bool:
    parts = [p.lower() for p in _KEY_PART.findall(key[:200])]
    for i, part in enumerate(parts):
        if part in _SENSITIVE_PARTS or part.endswith(_SENSITIVE_SUFFIXES) or part.startswith(("password", "passwd")):
            return True
        if i + 1 < len(parts) and (part, parts[i + 1]) in _SENSITIVE_PAIRS:
            return True
    if parts and parts[-1] == "pass":
        return len(parts) == 1 or parts[-2] not in _NON_SECRET_PASS_QUALIFIERS
    return False


def _kv_repl(counts: dict[str, int] | None) -> Callable[[re.Match[str]], str]:
    def repl(m: re.Match[str]) -> str:
        v = m["v"]
        kw = (m["kw"] or m.groupdict().get("kwl") or "").lower()
        quote = v[0] if v[0] in "\"'" else ""
        inner = v[1:-1] if quote and len(v) >= 2 and v[-1] == quote else v
        # `token: expired` / `tests pass: 15` are prose, not credentials: for the generic words "token" and
        # "pass" only, keep plain lowercase words and bare numbers (max_tokens=100). Passwords and explicit
        # key names are always redacted.
        if kw in ("token", "pass") and (_LOWER_WORD.fullmatch(inner) or inner.isdigit()):
            return m.group(0)
        if counts is not None:
            counts["secret_kv"] = counts.get("secret_kv", 0) + 1
        return f"{m['k']}{quote}[REDACTED:secret]{quote if quote and v[-1] == quote and len(v) >= 2 else ''}"

    return repl


def redact(text: str, counts: dict[str, int] | None = None) -> str:
    """Return `text` with credentials and personal data replaced by markers.

    If `counts` is given it is incremented per rule that fired.
    """
    if not text:
        return text
    for name, pattern, repl in _RULES:
        text, n = pattern.subn(repl, text)
        if n and counts is not None:
            counts[name] = counts.get(name, 0) + n
    text = _KV.sub(_kv_repl(counts), text)
    text = _KV_CAMEL.sub(_kv_repl(counts), text)

    def _card(m: re.Match[str]) -> str:
        digits = re.sub(r"[ -]", "", m.group(0))
        if 13 <= len(digits) <= 19 and _luhn_ok(digits):
            if counts is not None:
                counts["card_number"] = counts.get("card_number", 0) + 1
            return "[REDACTED:card_number]"
        return m.group(0)

    return _CARD.sub(_card, text)


REDACTED_VALUE = "[REDACTED:secret]"


def scrub_structure(obj: object, depth: int = 0) -> object:
    """Copy of parsed JSON with the value of every sensitive key (at any depth) replaced."""
    if depth > 8:
        return "[truncated]"
    if isinstance(obj, dict):
        return {k: (REDACTED_VALUE if isinstance(k, str) and is_sensitive_key(k) else scrub_structure(v, depth + 1))
                for k, v in obj.items()}
    if isinstance(obj, list):
        return [scrub_structure(v, depth + 1) for v in obj[:200]]
    return obj
