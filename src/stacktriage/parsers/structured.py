"""Structured inputs: JSON Lines and CSV telemetry exports.

CSV preserves quoted multiline content and reports physical record-start lines.
Malformed CSV quoting stops parsing with a diagnostic; excess columns are capped
without discarding otherwise valid records. Nested JSON error objects contribute
their stack text, not a serialized metadata object.
"""
from __future__ import annotations

import csv
import json
import math
from collections.abc import Iterator
from typing import Any

from ..models import Diagnostics, Level, Limits, LogEvent
from ..redaction import REDACTED_VALUE, is_sensitive_key, redact, scrub_structure
from .common import normalize_level, parse_timestamp

TS_KEYS = ("timestamp", "@timestamp", "time", "ts", "datetime", "asctime", "date", "t")
LEVEL_KEYS = ("level", "severity", "levelname", "log_level", "loglevel", "log.level", "lvl", "priority")
MSG_KEYS = ("message", "msg", "event", "text", "log", "description", "event_type", "error_message")
# host/hostname/device are deliberately NOT service keys: they stay as evidence fields, so
# one error seen on two hosts remains one group.
SERVICE_KEYS = ("service", "service.name", "service_name", "app", "application", "app_name",
                "component", "name")
LOGGER_KEYS = ("logger", "logger_name", "module", "category")
TRACE_KEYS = ("stack_trace", "stacktrace", "stack", "exception", "traceback", "exc_info", "error.stack")
ERR_OBJ_KEYS = ("err", "error", "exception")
_PINO_MARKERS = ("pid", "hostname", "v")


def _get(d: dict[str, Any], key: str) -> Any:
    if key in d:
        return d[key]
    if "." in key:
        cur: Any = d
        for part in key.split("."):
            if not isinstance(cur, dict) or part not in cur:
                return None
            cur = cur[part]
        return cur
    return None


def _pick(d: dict[str, Any], keys: tuple[str, ...]) -> tuple[str | None, Any]:
    for k in keys:
        v = _get(d, k)
        if v is not None and v != "" and v != []:
            return k, v
    return None, None


def _text(v: Any) -> str:
    if isinstance(v, str):
        return v
    if isinstance(v, list) and all(isinstance(x, str) for x in v):
        return "\n".join(v)
    try:
        return json.dumps(v, ensure_ascii=False, separators=(",", ":"), default=str)
    except (TypeError, ValueError, RecursionError):
        return str(v)


def _clip(s: str, n: int) -> str:
    return s if len(s) <= n else s[:n] + "… [truncated]"


def _echo(v: object, n: int = 60) -> str:
    """Safe rendering of an input value inside a diagnostic: redacted and short."""
    return _clip(redact(str(v)), n)


def _name(k: object, n: int = 64) -> str:
    """Field / column names come from untrusted input: redact and bound them."""
    return _clip(redact(str(k)), n)


def _store_value(key: str, value: Any, limits: Limits) -> str:
    if is_sensitive_key(key):
        return REDACTED_VALUE
    return _clip(redact(_text(value)), limits.max_field_chars)


def parse_jsonl(
    lines: Iterator[tuple[int, str]], *, default_service: str | None, limits: Limits, diag: Diagnostics,
) -> Iterator[LogEvent]:
    for line_no, line in lines:
        s = line.strip()
        if not s:
            continue
        try:
            obj = json.loads(s)
        except (ValueError, RecursionError) as e:
            diag.add("INVALID_JSON", line_no, f"line is not valid JSON ({type(e).__name__})")
            continue
        if not isinstance(obj, dict):
            diag.add("NOT_AN_OBJECT", line_no, "JSON line is not an object; skipped")
            continue
        obj = scrub_structure(obj)  # sensitive keys at ANY depth lose their values
        counts: dict[str, int] = {}
        used: set[str] = set()

        ts_key, ts_val = _pick(obj, TS_KEYS)
        lvl_key, lvl_val = _pick(obj, LEVEL_KEYS)
        msg_key, msg_val = _pick(obj, MSG_KEYS)
        svc_key, svc_val = _pick(obj, SERVICE_KEYS)
        lg_key, lg_val = _pick(obj, LOGGER_KEYS)
        tr_key, tr_val = _pick(obj, TRACE_KEYS)
        if isinstance(tr_val, dict) and tr_key in ERR_OBJ_KEYS:
            tr_val = None  # unwrap error objects below, rather than serializing their metadata
        used.update(k.split(".")[0] for k in (ts_key, lvl_key, msg_key, svc_key, lg_key, tr_key) if k)

        # nested error objects: {"err": {"type":..., "message":..., "stack":...}}
        err_msg = err_type = None
        for ek in ERR_OBJ_KEYS:
            ev = obj.get(ek)
            if isinstance(ev, dict):
                used.add(ek)
                err_msg = ev.get("message") or err_msg
                candidate_type = ev.get("type") or ev.get("name")
                if isinstance(candidate_type, str):
                    err_type = candidate_type
                if tr_val is None:
                    tr_val = ev.get("stack") or ev.get("stack_trace") or ev.get("stacktrace")
            elif isinstance(ev, str) and tr_val is None and "\n" in ev:
                used.add(ek)
                tr_val = ev
            elif isinstance(ev, str) and msg_val is None:
                used.add(ek)
                err_msg = ev

        ts = parse_timestamp(ts_val if isinstance(ts_val, (str, int, float)) else None)
        if ts is None:
            diag.events_without_timestamp += 1
            diag.add("MISSING_TIMESTAMP" if ts_val is None else "BAD_TIMESTAMP", line_no,
                     "record has no timestamp" if ts_val is None else f"could not parse timestamp {_echo(ts_val)!r}")
        elif isinstance(ts_val, str) and not any(c in ts_val[10:] for c in "Z+-"):
            diag.add("TIMEZONE_ASSUMED_UTC", line_no, "timestamp has no timezone; interpreted as UTC")

        pino = any(m in obj for m in _PINO_MARKERS)
        level = normalize_level(lvl_val, pino_scale=pino)
        if lvl_val is None or level is Level.UNKNOWN:
            diag.add("MISSING_LEVEL" if lvl_val is None else "UNKNOWN_LEVEL", line_no,
                     "no level field" if lvl_val is None else f"unrecognized level {_echo(lvl_val)!r}")

        message = _text(msg_val) if msg_val is not None else ""
        if not message and err_msg:
            message = _text(err_msg)
        if err_type and err_type not in message:
            message = f"{err_type}: {message}" if message else str(err_type)
        message = redact(message, counts)
        message = _clip(message, limits.max_message_chars)
        stack = None
        if tr_val is not None:
            stack = redact(_clip(_text(tr_val), limits.max_stack_chars), counts)
        if not message and stack:
            message = stack.splitlines()[0].strip()
        if not message:
            diag.add("MISSING_MESSAGE", line_no, "record has no message")

        fields: dict[str, str] = {}
        metrics: dict[str, float] = {}
        for k, v in obj.items():
            if k in used or k in ("v",) or (pino and k in ("pid", "hostname", "level", "time")):
                continue
            if isinstance(v, bool) or v is None:
                if isinstance(v, bool) and len(fields) < limits.max_fields:
                    fields[_name(k)] = str(v).lower()
            elif isinstance(v, (int, float)) and not is_sensitive_key(k):
                try:
                    num = float(v)
                except OverflowError:
                    continue
                if math.isfinite(num) and len(metrics) < limits.max_metrics:
                    metrics[_name(k)] = num
            elif isinstance(v, (str, dict, list)) and len(fields) < limits.max_fields:
                fields[_name(k)] = _store_value(k, v, limits)
        diag.count_redactions(counts)

        service = _text(svc_val) if svc_val is not None else default_service
        yield LogEvent(
            event_id="", line_no=line_no, source_format="jsonl", timestamp=ts, level=level,
            service=_clip(redact(service), 100) if service else None,
            logger=_clip(redact(_text(lg_val)), 200) if lg_val is not None else None,
            message=message, stack_trace=stack, fields=fields, metrics=metrics,
        )


def parse_csv(
    lines: Iterator[tuple[int, str]], *, default_service: str | None, limits: Limits, diag: Diagnostics,
) -> Iterator[LogEvent]:
    physical_line = record_start = 0

    def csv_lines() -> Iterator[str]:
        nonlocal physical_line, record_start
        for source_line, text in lines:
            if not record_start:
                record_start = source_line
            physical_line = source_line
            yield text + "\n"

    reader = csv.reader(csv_lines(), strict=True)
    header: list[str] | None = None
    header_width = 0
    cols: dict[str, int] = {}
    try:
        for row in reader:
            line_no = record_start
            record_start = 0
            if header is None:
                if not any(c.strip() for c in row):
                    continue
                header_width = len(row)
                if header_width > limits.max_csv_columns:
                    diag.add("CSV_COLUMNS_CAPPED", line_no,
                             f"only the first {limits.max_csv_columns} of {header_width} columns are retained")
                header = [c.strip() for c in row][: limits.max_csv_columns]
                lowered = [h.lower() for h in header]
                for role, keys in (("ts", TS_KEYS), ("level", LEVEL_KEYS), ("msg", MSG_KEYS),
                                   ("service", SERVICE_KEYS), ("logger", LOGGER_KEYS), ("trace", TRACE_KEYS)):
                    for k in keys:
                        if k in lowered:
                            cols[role] = lowered.index(k)
                            break
                continue
            if not row or not any(c.strip() for c in row):
                continue
            if len(row) != header_width:
                diag.add("BAD_ROW_WIDTH", line_no, f"row has {len(row)} fields, header has {header_width}")
                continue
            counts: dict[str, int] = {}

            def cell(role: str) -> str:
                i = cols.get(role)
                return row[i].strip() if i is not None else ""

            ts_raw = cell("ts")
            ts = parse_timestamp(ts_raw) if ts_raw else None
            if ts is None:
                diag.events_without_timestamp += 1
                diag.add("MISSING_TIMESTAMP" if not ts_raw else "BAD_TIMESTAMP", line_no,
                         "row has no timestamp" if not ts_raw else f"could not parse timestamp {_echo(ts_raw)!r}")
            lvl_raw = cell("level")
            level = normalize_level(lvl_raw) if lvl_raw else Level.UNKNOWN
            if level is Level.UNKNOWN:
                diag.add("MISSING_LEVEL" if not lvl_raw else "UNKNOWN_LEVEL", line_no,
                         "row has no level" if not lvl_raw else f"unrecognized level {_echo(lvl_raw)!r}")
            message = _clip(redact(cell("msg"), counts), limits.max_message_chars)
            trace = cell("trace")
            stack = redact(_clip(trace, limits.max_stack_chars), counts) if trace else None

            special = set(cols.values())
            fields: dict[str, str] = {}
            metrics: dict[str, float] = {}
            for i, name in enumerate(header):
                if i in special or not row[i].strip():
                    continue
                raw = row[i].strip()
                try:
                    num = float(raw)
                except (ValueError, OverflowError):
                    num = None
                if num is not None and math.isfinite(num) and not is_sensitive_key(name):
                    if len(metrics) < limits.max_metrics:
                        metrics[_name(name)] = num
                elif len(fields) < limits.max_fields:
                    fields[_name(name)] = _store_value(name, raw, limits)
            diag.count_redactions(counts)
            service = cell("service") or default_service
            yield LogEvent(
                event_id="", line_no=line_no, source_format="csv", timestamp=ts, level=level,
                service=_clip(redact(service), 100) if service else None,
                logger=_clip(redact(cell("logger")), 200) or None, message=message, stack_trace=stack,
                fields=fields, metrics=metrics,
            )
    except csv.Error as e:
        diag.add("CSV_ERROR", physical_line, f"CSV parsing stopped: {e}")
