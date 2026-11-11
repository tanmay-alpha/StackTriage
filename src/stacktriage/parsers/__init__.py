"""Format detection and the public `parse` entry point."""
from __future__ import annotations

import csv
import io
import itertools
import json
import os
from collections.abc import Iterator
from typing import BinaryIO

from ..models import DEFAULT_LIMITS, Diagnostics, Limits, LogEvent, UnsupportedFormatError
from .common import finalize, read_lines
from .structured import LEVEL_KEYS, MSG_KEYS, TS_KEYS, parse_csv, parse_jsonl
from .text import detect_family, has_headerless_trace, parse_text

FORMATS = ("python", "node", "java", "jsonl", "csv", "text")
_SAMPLE_LINES = 200


class ParseSession:
    """Detected format, diagnostics and a lazy, bounded stream of events.

    Iterate once. `diagnostics` is complete only after iteration finishes.
    """

    def __init__(self, fmt: str, diagnostics: Diagnostics, events: Iterator[LogEvent]):
        self.format = fmt
        self.diagnostics = diagnostics
        self._events = events

    def __iter__(self) -> Iterator[LogEvent]:
        return self._events


def _sniff_json(sample: list[str]) -> bool:
    ok = 0
    for ln in sample:
        s = ln.strip()
        if s.startswith("{") and s.endswith("}"):
            try:
                if isinstance(json.loads(s), dict):
                    ok += 1
            except (ValueError, RecursionError):
                pass
    return bool(sample) and ok / len(sample) >= 0.6


def _sniff_csv(sample: list[str], filename: str | None) -> bool:
    if not sample:
        return False
    try:
        header = next(csv.reader([sample[0]]))
    except (csv.Error, StopIteration):
        return False
    names = {h.strip().lower() for h in header}
    known = names & set(TS_KEYS + LEVEL_KEYS + MSG_KEYS)
    by_ext = bool(filename) and filename.lower().endswith(".csv")
    return len(header) >= 2 and (len(known) >= 2 or (by_ext and len(known) >= 1))


def parse(
    stream: BinaryIO,
    *,
    filename: str | None = None,
    format: str | None = None,
    default_service: str | None = None,
    limits: Limits = DEFAULT_LIMITS,
) -> ParseSession:
    """Detect the format of a binary stream and return a lazy event stream.

    Raises UnsupportedFormatError for binary, empty or unrecognized input.
    Input is only ever read as data; nothing in it is executed or evaluated.
    `stream` must be seekable (open file, SpooledTemporaryFile, BytesIO).
    """
    if format is not None and format not in FORMATS:
        raise ValueError(f"unknown format {format!r}; expected one of {FORMATS}")
    head = stream.read(8192)
    stream.seek(0)
    if b"\x00" in head:
        raise UnsupportedFormatError("input looks binary (NUL bytes); expected UTF-8 text")

    diag = Diagnostics()
    lines = read_lines(stream, limits, diag)
    # Skip only leading whitespace; blank lines inside CSV fields are data.
    first = next((p for p in lines if p[1].strip()), None)
    if first is None:
        raise UnsupportedFormatError("input is empty")
    sample_pairs = [first, *itertools.islice(lines, _SAMPLE_LINES - 1)]
    sample = [t for _, t in sample_pairs if t.strip()]
    rest = itertools.chain(sample_pairs, lines)

    family: str | None = None
    fmt = format
    if fmt is None:
        if _sniff_json(sample):
            fmt = "jsonl"
        elif _sniff_csv(sample, filename):
            fmt = "csv"
        else:
            family, headers = detect_family(sample)
            if headers == 0 and not has_headerless_trace(sample):
                raise UnsupportedFormatError(
                    "no supported log format recognized (expected Python, Node.js/Express, "
                    "Java/Spring Boot text logs, JSON Lines, or CSV)"
                )
            fmt = family or "text"
    elif fmt in ("python", "node", "java"):
        family = fmt
    elif fmt == "text":
        family = None

    if fmt == "jsonl":
        raw: Iterator[LogEvent] = parse_jsonl(rest, default_service=default_service, limits=limits, diag=diag)
    elif fmt == "csv":
        raw = parse_csv(rest, default_service=default_service, limits=limits, diag=diag)
    else:
        raw = parse_text(rest, family=family, default_service=default_service, limits=limits, diag=diag)

    metric_names: set[str] = set()

    def bounded() -> Iterator[LogEvent]:
        for ev in finalize(raw):
            if ev.metrics:
                kept = {}
                for k, v in ev.metrics.items():
                    if k in metric_names or len(metric_names) < limits.max_metric_names:
                        metric_names.add(k)
                        kept[k] = v
                    elif "METRIC_NAMES_CAPPED" not in diag.code_counts:
                        diag.add("METRIC_NAMES_CAPPED", ev.line_no,
                                 f"more than {limits.max_metric_names} distinct metric names; extra names ignored")
                    else:
                        diag.code_counts["METRIC_NAMES_CAPPED"] += 1
                ev.metrics = kept
            if diag.events_parsed >= limits.max_events:
                diag.truncated = True
                diag.truncated_reason = f"event limit of {limits.max_events} reached"
                diag.add("EVENT_LIMIT", ev.line_no, diag.truncated_reason)
                return
            diag.events_parsed += 1
            yield ev
        if diag.events_parsed == 0 and not diag.truncated:
            diag.add("NO_EVENTS", None, "input was readable but produced no records")

    return ParseSession(fmt, diag, bounded())


def parse_file(path: str | os.PathLike[str], **kwargs) -> tuple[ParseSession, io.BufferedReader]:
    """Convenience: open `path` and parse it. Caller must close the returned handle."""
    fh = open(path, "rb")  # noqa: SIM115 - handle outlives this call by design
    try:
        kwargs.setdefault("filename", os.path.basename(os.fspath(path)))
        return parse(fh, **kwargs), fh
    except BaseException:
        fh.close()
        raise
