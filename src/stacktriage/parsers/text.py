"""Line-oriented text logs: Python, Node.js/Express, Java/Spring Boot.

A record starts at a line matching a known header pattern. Every following line
that does not match a header is a continuation (stack trace or multiline
payload) of the current record.
"""
from __future__ import annotations

import re
from collections import Counter, deque
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import datetime

from ..fingerprint import exception_summary
from ..models import Diagnostics, Level, Limits, LogEvent
from ..redaction import redact
from .common import normalize_level, parse_timestamp

_TS = r"(?P<ts>\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d{1,9})?(?:Z|[+-]\d{2}:?\d{2})?)"
_LVL = r"(?P<level>TRACE|DEBUG|INFO|NOTICE|WARN(?:ING)?|ERROR|SEVERE|CRITICAL|FATAL)"
_HAS_TZ = re.compile(r"(?:Z|[+-]\d{2}:?\d{2})$")


@dataclass(frozen=True, slots=True)
class HeaderPattern:
    name: str
    family: str | None  # python | node | java | None (family-neutral)
    regex: re.Pattern[str]


def _p(name: str, family: str | None, pat: str, flags: int = 0) -> HeaderPattern:
    return HeaderPattern(name, family, re.compile(pat, flags))


# Order matters: most specific first.
HEADERS: list[HeaderPattern] = [
    _p("spring", "java",
       rf"^{_TS}\s+{_LVL}\s+(?P<pid>\d+)\s+---\s+(?:\[(?P<app>[^\]]*)\]\s+)?"
       r"\[(?P<thread>[^\]]*)\]\s+(?P<logger>\S+)\s*:\s?(?P<msg>.*)$"),
    _p("logback", "java",
       rf"^{_TS}\s+\[(?P<thread>[^\]]*)\]\s+{_LVL}\s+(?P<logger>[\w.$]+)\s+-\s+(?P<msg>.*)$"),
    _p("log4j", "java",
       rf"^{_TS}\s+{_LVL}\s+(?P<logger>(?:[a-z_][\w$]*\.)+[\w$]+)\s+-\s+(?P<msg>.*)$"),
    _p("py_name_level", "python",
       rf"^{_TS}\s+-\s+(?P<logger>[\w.\-]+)\s+-\s+{_LVL}\s+-\s+(?P<msg>.*)$"),
    _p("py_level", "python", rf"^{_TS}\s+-\s+{_LVL}\s+-\s+(?P<msg>.*)$"),
    _p("py_flask", "python", rf"^\[{_TS}\]\s+{_LVL}\s+in\s+(?P<logger>\S+):\s+(?P<msg>.*)$"),
    _p("py_colon", "python", rf"^{_TS}\s+{_LVL}\s+(?P<logger>[\w.\-]+):\s+(?P<msg>.*)$"),
    _p("py_basic", "python", r"^(?P<level>DEBUG|INFO|WARNING|ERROR|CRITICAL):(?P<logger>[\w.\-]+):(?P<msg>.*)$"),
    _p("node_clf", "node",
       r'^(?P<ip>\S+) \S+ \S+ \[(?P<ts>[^\]]+)\] "(?P<req>[A-Z]+ [^"]*?)(?: HTTP/[\d.]+)?" '
       r"(?P<status>\d{3}) (?:\d+|-)"),
    _p("node_service", "node",
       rf"^{_TS}\s+\[(?P<service>[^\]]+)\]\s+{_LVL}:\s*(?P<msg>.*)$", re.I),
    _p("node_winston", "node", rf"^{_TS}\s+{_LVL}:\s*(?P<msg>.*)$", re.I),
    _p("node_bracket", "node",
       rf"^\[{_TS}\]\s+{_LVL}:?\s+(?:\[(?P<service>[^\]]+)\]\s+)?(?P<msg>.*)$", re.I),
    _p("generic", None, rf"^{_TS}\s+\[?{_LVL}\]?[\s:\-]+(?P<msg>.*)$"),
]

_PY_SIGNAL = re.compile(r'^Traceback \(most recent call last\):|^\s+File "[^"]+", line \d+')
_JAVA_SIGNAL = re.compile(r"^\s+at [\w.$<>]+\([^)]*\.(?:java|kt|scala|groovy)(?::\d+)?\)|^Caused by: ")
_NODE_SIGNAL = re.compile(r"^\s+at .+[:(][^:]+:\d+:\d+\)?$|node:internal")
_HEADERLESS = re.compile(
    r'^(?:Traceback \(most recent call last\):|Exception in thread "[^"]*" \S+)'
)


def match_header(line: str) -> tuple[HeaderPattern, re.Match[str]] | None:
    # cheap pre-filter: all headers have a digit (timestamp / status) or an uppercase level prefix
    for hp in HEADERS:
        m = hp.regex.match(line)
        if m:
            return hp, m
    return None


def detect_family(sample: Iterable[str]) -> tuple[str | None, int]:
    """Return (family, header_matches) from a sample of lines."""
    votes: Counter[str] = Counter()
    headers = 0
    for line in sample:
        hm = match_header(line)
        if hm:
            headers += 1
            if hm[0].family:
                votes[hm[0].family] += 2
            continue
        if _PY_SIGNAL.match(line):
            votes["python"] += 1
        elif _JAVA_SIGNAL.match(line):
            votes["java"] += 1
        elif _NODE_SIGNAL.match(line):
            votes["node"] += 1
    if not votes:
        return None, headers
    return votes.most_common(1)[0][0], headers


def has_headerless_trace(sample: Iterable[str]) -> bool:
    return any(_HEADERLESS.match(ln) for ln in sample)


class _Cont:
    """Continuation lines of one record, bounded: keeps the first half and the last
    half of the allowed lines (the exception line / root `Caused by:` sit at the END of
    a trace), plus any `Caused by:` headers that fell in the omitted middle."""

    __slots__ = ("head_n", "tail_n", "head", "tail", "dropped", "kept")

    def __init__(self, limit: int) -> None:
        self.head_n = max(limit // 2, 1)
        self.tail_n = max(limit - self.head_n, 1)
        self.head: list[str] = []
        self.tail: deque[str] = deque()
        self.dropped = 0
        self.kept: list[str] = []

    def add(self, line: str) -> None:
        if len(self.head) < self.head_n:
            self.head.append(line)
            return
        if len(self.tail) >= self.tail_n:
            old = self.tail.popleft()
            self.dropped += 1
            if old.startswith("Caused by:") and len(self.kept) < 20:
                self.kept.append(old)
        self.tail.append(line)

    def lines(self) -> list[str]:
        out = list(self.head)
        if self.dropped:
            out.append(f"... [{self.dropped} lines omitted]")
            out += self.kept
        out += self.tail
        return out

    def __bool__(self) -> bool:
        return bool(self.head)


@dataclass(slots=True)
class _Pending:
    line_no: int
    ts_raw: str | None
    level: Level
    service: str | None
    logger: str | None
    msg: str
    fields: dict[str, str]
    cont: _Cont
    headerless: bool = False


def parse_text(
    lines: Iterator[tuple[int, str]],
    *,
    family: str | None,
    default_service: str | None,
    limits: Limits,
    diag: Diagnostics,
) -> Iterator[LogEvent]:
    fmt = family or "text"
    cur: _Pending | None = None
    orphan_lines = 0

    def emit(p: _Pending) -> LogEvent | None:
        counts: dict[str, int] = {}
        stack = None
        msg = p.msg
        if p.cont:
            text = "\n".join(p.cont.lines())
            if p.cont.dropped:
                diag.add("STACK_TRUNCATED", p.line_no, f"{p.cont.dropped} continuation lines omitted from the middle")
            if len(text) > limits.max_stack_chars:
                text = text[: limits.max_stack_chars] + "\n... [truncated]"
            stack = redact(text, counts)
        if p.headerless:
            msg = (exception_summary(stack or "") or (p.cont.head[0].strip() if p.cont else ""))
        msg = redact(msg, counts)
        if len(msg) > limits.max_message_chars:
            msg = msg[: limits.max_message_chars] + "… [truncated]"
        if not msg and stack:
            msg = stack.splitlines()[0].strip()
        ts: datetime | None = parse_timestamp(p.ts_raw) if p.ts_raw else None
        if p.ts_raw and ts is None:
            diag.add("BAD_TIMESTAMP", p.line_no, f"could not parse timestamp {p.ts_raw!r}")
        elif p.ts_raw and not _HAS_TZ.search(p.ts_raw):
            diag.add("TIMEZONE_ASSUMED_UTC", p.line_no, "timestamp has no timezone; interpreted as UTC")
        if ts is None:
            diag.events_without_timestamp += 1
            diag.add("MISSING_TIMESTAMP", p.line_no, "record has no usable timestamp")
        diag.count_redactions(counts)
        fields = {k: redact(v, None)[: limits.max_field_chars] for k, v in p.fields.items() if v}
        return LogEvent(
            event_id="", line_no=p.line_no, source_format=fmt, timestamp=ts, level=p.level,
            service=p.service or default_service, logger=p.logger, message=msg,
            stack_trace=stack, fields=fields,
        )

    for line_no, line in lines:
        if not line.strip():
            continue
        hm = match_header(line)
        if hm:
            if cur is not None:
                ev = emit(cur)
                if ev:
                    yield ev
            hp, m = hm
            gd = m.groupdict()
            fields: dict[str, str] = {}
            if hp.name == "node_clf":
                status = int(gd["status"])
                level = Level.ERROR if status >= 500 else Level.WARNING if status >= 400 else Level.INFO
                msg = f"HTTP {status} {gd['req']}"
                fields["status"] = str(status)
            else:
                level = normalize_level(gd["level"])
                msg = gd.get("msg") or ""
            service = gd.get("service")
            if hp.name == "spring":
                app = (gd.get("app") or "").split(",")[0].strip()
                service = app or None
                fields["thread"] = gd["thread"]
                fields["pid"] = gd["pid"]
            elif gd.get("thread"):
                fields["thread"] = gd["thread"]
            cur = _Pending(line_no, gd.get("ts"), level, service, gd.get("logger"), msg.strip(), fields, _Cont(limits.max_stack_lines))
            continue
        if cur is None or (cur.headerless and _HEADERLESS.match(line)):
            if _HEADERLESS.match(line):
                if cur is not None:
                    ev = emit(cur)
                    if ev:
                        yield ev
                cur = _Pending(line_no, None, Level.ERROR, None, None, "", {}, _Cont(limits.max_stack_lines), headerless=True)
                cur.cont.add(line)
            else:
                orphan_lines += 1
                if orphan_lines <= 3:
                    diag.add("ORPHAN_LINE", line_no, "line before any recognizable record header was skipped")
            continue
        cur.cont.add(line)
    if cur is not None:
        ev = emit(cur)
        if ev:
            yield ev
    if orphan_lines > 3:
        diag.code_counts["ORPHAN_LINE"] = orphan_lines
