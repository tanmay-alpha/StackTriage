"""Credential and personal-data redaction.

Applied by parsers before any text is stored in an event, so unredacted values
never reach the database, API responses, or exports. Pattern-based redaction is
best effort: it catches common credential shapes, not arbitrary secrets.
"""
from __future__ import annotations

import re

# (name, pattern, replacement). Order matters: specific shapes before generic ones.
_PEM = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)", re.S
)
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
            r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}"
            r"|xox[abprs]-[A-Za-z0-9-]{10,}|sk-[A-Za-z0-9_-]{20,}|AIza[0-9A-Za-z_-]{30,})"
        ),
        "[REDACTED:api_token]",
    ),
    (
        "auth_header",
        re.compile(
            r"(?i)\b(authorization|proxy-authorization)(\s*[:=]\s*[\"']?)"
            r"(?:(?:bearer|basic|token|digest)\s+)?[^\s\"',;]+"
        ),
        r"\1\2[REDACTED:auth_header]",
    ),
    (
        "cookie",
        re.compile(r"(?i)\b(set-cookie|cookie)(\s*[:=]\s*)[^\r\n]+"),
        r"\1\2[REDACTED:cookie]",
    ),
    (
        "url_credentials",
        re.compile(r"(?i)\b([a-z][a-z0-9+.-]{1,20}://)[^\s/:@\"']{1,100}:[^\s/@\"']{1,200}@"),
        r"\1[REDACTED:credentials]@",
    ),
    (
        "secret_kv",
        # password=..., "api_key": "...", secret: ..., access_token=...
        re.compile(
            r"(?i)(?P<k>[\"']?\b[\w.-]{0,30}(?:password|passwd|pwd|secret|token|api[_-]?key"
            r"|apikey|access[_-]?key|private[_-]?key|client[_-]?secret|credential)s?[\w.-]{0,10}"
            r"[\"']?\s*[:=]\s*)(?P<q>[\"']?)(?P<v>[^\s\"',;&}\]]{1,500})(?P=q)"
        ),
        r"\g<k>\g<q>[REDACTED:secret]\g<q>",
    ),
    (
        "email",
        re.compile(r"\b[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,255}\.[A-Za-z]{2,24}\b"),
        "[REDACTED:email]",
    ),
]
_CARD = re.compile(r"\b(?:\d[ -]?){12,18}\d\b")

# Key names whose values are always redacted in structured (JSON/CSV) records.
_SENSITIVE_KEY = re.compile(
    r"(?i)(password|passwd|pwd|secret|token|api[_-]?key|apikey|authorization|cookie"
    r"|credential|private[_-]?key|session[_-]?id|ssn)"
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
    return bool(_SENSITIVE_KEY.search(key))


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

    def _card(m: re.Match[str]) -> str:
        digits = re.sub(r"[ -]", "", m.group(0))
        if 13 <= len(digits) <= 19 and _luhn_ok(digits):
            if counts is not None:
                counts["card_number"] = counts.get("card_number", 0) + 1
            return "[REDACTED:card_number]"
        return m.group(0)

    return _CARD.sub(_card, text)


REDACTED_VALUE = "[REDACTED:secret]"
