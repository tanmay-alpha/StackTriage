"""Shared helpers: bounded line reading, timestamp and level normalization."""
from __future__ import annotations

import hashlib
import re
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import BinaryIO

from ..models import Diagnostics, Level, Limits, LogEvent

ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class LimitReached(Exception):
    """Internal signal: a hard input limit stopped parsing."""


def read_lines(
    stream: BinaryIO, limits: Limits, diag: Diagnostics
) -> Iterator[tuple[int, str]]:
    """Yield (line_no, text) with bounded memory.

    Lines longer than `limits.max_line_chars` are truncated (remainder is
    skipped, not buffered). Stops, flagging diagnostics, at `max_bytes`.
    Decoding is lenient UTF-8; ANSI colour codes and control characters are
    stripped. Content is never interpreted or executed.
    """
    line_no = 0
    first = True
    chunk_limit = limits.max_line_chars * 4 + 1  # worst case UTF-8 bytes per char
    while True:
        raw = stream.readline(chunk_limit)
        if not raw:
            return
        diag.bytes_read += len(raw)
        if diag.bytes_read > limits.max_bytes:
            diag.truncated = True
            diag.truncated_reason = f"file exceeds {limits.max_bytes} bytes"
            diag.add("FILE_TOO_LARGE", line_no + 1, diag.truncated_reason)
            return
        line_no += 1
        diag.lines_read = line_no
        overlong = not raw.endswith(b"\n") and len(raw) >= chunk_limit
        if overlong:
            # discard the rest of this physical line without buffering it
            while True:
                rest = stream.readline(65536)
                diag.bytes_read += len(rest)
                if not rest or rest.endswith(b"\n"):
                    break
        text = raw.decode("utf-8", errors="replace")
        if first:
            text = text.lstrip("\ufeff")
            first = False
        text = text.rstrip("\r\n")
        if len(text) > limits.max_line_chars:
            text = text[: limits.max_line_chars]
            overlong = True
        if overlong:
            diag.add("LINE_TOO_LONG", line_no, f"line truncated to {limits.max_line_chars} characters")
        text = _CTRL_RE.sub("", ANSI_RE.sub("", text))
        yield line_no, text


# ---------------------------------------------------------------- timestamps
_TZ_FIX = re.compile(r"([+-]\d{2})(\d{2})$")


def parse_timestamp(value: str | int | float | None) -> datetime | None:
    """Parse ISO-8601-like strings and epoch numbers into aware UTC datetimes.

    Naive timestamps are interpreted as UTC. Returns None if unparseable.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return _from_epoch(float(value))
    s = value.strip().strip("[]")
    if not s:
        return None
    if re.fullmatch(r"\d{9,19}(?:\.\d+)?", s):
        return _from_epoch(float(s))
    s = s.replace(",", ".")
    if s.endswith(("Z", "z")):
        s = s[:-1] + "+00:00"
    s = _TZ_FIX.sub(r"\1:\2", s) if re.search(r"\d[+-]\d{4}$", s) else s
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        for fmt in ("%d/%b/%Y:%H:%M:%S %z", "%Y/%m/%d %H:%M:%S", "%Y-%m-%d %H:%M:%S %z"):
            try:
                dt = datetime.strptime(s, fmt)
                break
            except ValueError:
                continue
        else:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    try:
        return dt.astimezone(UTC)
    except (OverflowError, ValueError):
        return None


def _from_epoch(x: float) -> datetime | None:
    # Heuristic by magnitude: seconds, milliseconds, microseconds, nanoseconds.
    ax = abs(x)
    if ax >= 1e17:
        x /= 1e9
    elif ax >= 1e14:
        x /= 1e6
    elif ax >= 1e11:
        x /= 1e3
    try:
        dt = datetime.fromtimestamp(x, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None
    return dt if 1990 <= dt.year <= 2100 else None


# -------------------------------------------------------------------- levels
_LEVEL_WORDS = {
    "trace": Level.DEBUG, "debug": Level.DEBUG, "finest": Level.DEBUG, "finer": Level.DEBUG,
    "fine": Level.DEBUG, "verbose": Level.DEBUG, "silly": Level.DEBUG,
    "info": Level.INFO, "information": Level.INFO, "notice": Level.INFO, "log": Level.INFO,
    "http": Level.INFO,
    "warn": Level.WARNING, "warning": Level.WARNING,
    "error": Level.ERROR, "err": Level.ERROR, "severe": Level.ERROR,
    "critical": Level.CRITICAL, "crit": Level.CRITICAL, "fatal": Level.CRITICAL,
    "emerg": Level.CRITICAL, "emergency": Level.CRITICAL, "alert": Level.CRITICAL,
    "panic": Level.CRITICAL,
}
_PINO = {10: Level.DEBUG, 20: Level.DEBUG, 30: Level.INFO, 40: Level.WARNING,
         50: Level.ERROR, 60: Level.CRITICAL}
_PYNUM = {10: Level.DEBUG, 20: Level.INFO, 30: Level.WARNING, 40: Level.ERROR, 50: Level.CRITICAL}


def normalize_level(value: object, pino_scale: bool = False) -> Level:
    if isinstance(value, bool) or value is None:
        return Level.UNKNOWN
    if isinstance(value, (int, float)):
        return (_PINO if pino_scale else _PYNUM).get(int(value), Level.UNKNOWN)
    s = str(value).strip().lower()
    if s.isdigit():
        return normalize_level(int(s), pino_scale)
    return _LEVEL_WORDS.get(s, Level.UNKNOWN)


# ----------------------------------------------------------------------- ids
def _content_digest(ev: LogEvent) -> bytes:
    h = hashlib.sha256()
    for part in (
        ev.source_format, ev.timestamp.isoformat() if ev.timestamp else "", str(int(ev.level)),
        ev.service or "", ev.message, ev.stack_trace or "",
    ):
        h.update(part.encode("utf-8", "replace"))
        h.update(b"\x1f")
    return h.digest()


def finalize(events: Iterator[LogEvent]) -> Iterator[LogEvent]:
    """Assign deterministic event ids (events arrive with event_id='').

    Id = hash(redacted content, occurrence index). Two identical records in one
    file get different ids (occurrence 0, 1, ...), but re-importing the same file,
    or an overlapping export that shifts line numbers, yields the same ids for
    the same records. Only 32-byte digests are kept in memory, not the text.
    """
    seen: dict[bytes, int] = {}
    for ev in events:
        digest = _content_digest(ev)
        n = seen.get(digest, 0)
        seen[digest] = n + 1
        ev.event_id = hashlib.sha256(digest + n.to_bytes(4, "big")).hexdigest()[:32]
        yield ev
