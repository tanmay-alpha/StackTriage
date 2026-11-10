"""Normalized data structures shared by parsers, grouping and timeline code."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import IntEnum


class Level(IntEnum):
    """Normalized severity of a log record. Ordered so comparisons work."""

    UNKNOWN = 0
    DEBUG = 10
    INFO = 20
    WARNING = 30
    ERROR = 40
    CRITICAL = 50


@dataclass(slots=True)
class LogEvent:
    """One logical log record (a header line plus any continuation lines).

    All text fields are already redacted and length-bounded by the parser.
    """

    event_id: str  # deterministic content hash, stable across re-imports
    line_no: int  # 1-based line (or CSV row) where the record starts
    source_format: str  # python | node | java | jsonl | csv
    timestamp: datetime | None  # timezone-aware UTC, None if the record had none
    level: Level
    service: str | None
    logger: str | None
    message: str
    stack_trace: str | None = None  # continuation lines: traces or multiline payloads
    fields: dict[str, str] = field(default_factory=dict)  # extra structured scalars
    metrics: dict[str, float] = field(default_factory=dict)  # numeric telemetry columns


@dataclass(slots=True)
class Issue:
    code: str
    line: int | None
    message: str


@dataclass(slots=True)
class Diagnostics:
    """Parsing report. `issues` keeps only the first few examples per code."""

    max_examples_per_code: int = 10
    lines_read: int = 0
    bytes_read: int = 0
    events_parsed: int = 0
    events_without_timestamp: int = 0
    truncated: bool = False
    truncated_reason: str | None = None
    code_counts: dict[str, int] = field(default_factory=dict)
    issues: list[Issue] = field(default_factory=list)
    redactions: dict[str, int] = field(default_factory=dict)

    def add(self, code: str, line: int | None, message: str) -> None:
        n = self.code_counts.get(code, 0) + 1
        self.code_counts[code] = n
        if n <= self.max_examples_per_code:
            self.issues.append(Issue(code, line, message))

    def count_redactions(self, counts: dict[str, int]) -> None:
        for k, v in counts.items():
            self.redactions[k] = self.redactions.get(k, 0) + v

    def to_dict(self) -> dict:
        return {
            "lines_read": self.lines_read,
            "bytes_read": self.bytes_read,
            "events_parsed": self.events_parsed,
            "events_without_timestamp": self.events_without_timestamp,
            "truncated": self.truncated,
            "truncated_reason": self.truncated_reason,
            "code_counts": dict(self.code_counts),
            "issues": [{"code": i.code, "line": i.line, "message": i.message} for i in self.issues],
            "redactions": dict(self.redactions),
        }


@dataclass(frozen=True, slots=True)
class Limits:
    max_bytes: int = 50 * 1024 * 1024
    max_events: int = 200_000
    max_line_chars: int = 65_536
    max_message_chars: int = 4_000
    max_stack_lines: int = 200
    max_stack_chars: int = 32_000
    max_fields: int = 20
    max_field_chars: int = 500
    max_metrics: int = 32  # per event
    max_metric_names: int = 64  # distinct names per file
    max_csv_columns: int = 200


DEFAULT_LIMITS = Limits()


class ParseError(Exception):
    """Base error for input that cannot be processed at all."""


class UnsupportedFormatError(ParseError):
    """The input is binary, empty, or matches no supported log format."""
