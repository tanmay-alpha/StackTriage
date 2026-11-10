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
        re.compile(
            r"(?i)\b(authorization|proxy-authorization)([\"']?\s*[:=]\s*[\"']?)"
            r"(?:[A-Za-z][\w-]{0,30}[ \t]+)?[^\s\"',;]+"
        ),
        r"\1\2[REDACTED:auth_header]",
    ),
    (
        "cookie",
        re.compile(r"(?i)\b(set-cookie|cookie)([\"']?\s*[:=]\s*)(?:\"[^\"\r\n]*\"|'[^'\r\n]*'|[^\r\n]+)"),
        r"\1\2[REDACTED:cookie]",
    ),
    (
        "url_credentials",
        # scheme://user:pass@host and scheme://:pass@host (redis); user may be empty
        re.compile(r"(?i)\b([a-z][a-z0-9+.-]{1,20}://)[^\s/:@\"']{0,100}:[^\s/@\"']{1,200}@"),
        r"\1[REDACTED:credentials]@",
    ),
    (
        "email",
        re.compile(r"\b[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,255}\.[A-Za-z]{2,24}\b"),
        "[REDACTED:email]",
    ),
]

_KEYWORDS = (
    r"password|passwd|passphrase|pass|pwd|secret|token|api[_-]?key|apikey|access[_-]?key|account[_-]?key"
    r"|private[_-]?key|client[_-]?secret|credentials?|session[_-]?id|sessionid"
)
_VALUE = (
    r"(?P<v>\"(?:[^\"\\\r\n]|\\.){0,500}\"|'(?:[^'\\\r\n]|\\.){0,500}'|[^\s\"',;&}\])]{1,500})"
)
# key=value / "key": "value". The keyword must END the key (so `password.js:22` is not a key)
# and be delimited from preceding text (so `bypass=1` is not a key).
_KV = re.compile(
    rf"(?i)(?P<k>[\"']?(?:[\w.-]{{0,30}}[_.-])?(?<![A-Za-z0-9])(?P<kw>{_KEYWORDS})s?[\"']?\s*[:=]\s*){_VALUE}"
)
# camelCase keys: userPassword, dbSecret, accessToken
_KV_CAMEL = re.compile(
    r"(?P<k>[\"']?[a-z][A-Za-z0-9]{0,30}(?P<kw>Password|Passwd|Secret|Token|ApiKey|AccessKey|PrivateKey)[\"']?\s*[:=]\s*)"
    + _VALUE
)
_CARD = re.compile(r"\b(?:\d[ -]?){12,18}\d\b")
_LOWER_WORD = re.compile(r"[a-z]+")

# Key names whose values are always redacted in structured (JSON/CSV) records.
_SENSITIVE_KEY = re.compile(
    r"(?i)(password|passwd|passphrase|pwd|secret|token|api[_-]?key|apikey|authorization|cookie"
    r"|credential|private[_-]?key|session[_-]?id|sessionid|account[_-]?key|ssn)"
)


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
    return bool(_SENSITIVE_KEY.search(key[:200]))


def _kv_repl(counts: dict[str, int] | None) -> Callable[[re.Match[str]], str]:
    def repl(m: re.Match[str]) -> str:
        v = m["v"]
        quote = v[0] if v[0] in "\"'" else ""
        inner = v[1:-1] if quote and len(v) >= 2 and v[-1] == quote else v
        # `token: expired` is prose, not a credential: for the generic word "token" only,
        # keep plain lowercase words and bare numbers (max_tokens=100). Passwords and explicit key names are always redacted.
        if m["kw"].lower() == "token" and (_LOWER_WORD.fullmatch(inner) or inner.isdigit()):
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
