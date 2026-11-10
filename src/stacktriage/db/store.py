"""Transactional persistence: idempotent ingestion and read queries."""
from __future__ import annotations

import dataclasses
import hashlib
import time
from collections import Counter
from datetime import datetime
from typing import Any, BinaryIO

from sqlalchemy import case, delete, func, insert, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from ..analysis import Analysis, analyze
from ..models import DEFAULT_LIMITS, Limits, LogEvent
from ..parsers import parse
from .models import STATUSES, Event, Group, Import, Note, Triage

_CHUNK = 2000


def now_ms() -> int:
    return int(time.time() * 1000)


def to_ms(dt: datetime | None) -> int | None:
    return None if dt is None else int(dt.timestamp() * 1000)


def content_key(file_sha256: str, fmt: str | None, service: str | None) -> str:
    """Identity of an ingestion run: same bytes + same parse options => same run."""
    return hashlib.sha256(f"{file_sha256}|{fmt or ''}|{service or ''}".encode()).hexdigest()


@dataclasses.dataclass(slots=True)
class IngestResult:
    import_id: int
    duplicate: bool


def _bucket_dict(b: Any) -> dict:
    d = {
        "start_ms": to_ms(b.start), "total": b.total, "debug": b.debug, "info": b.info,
        "warning": b.warning, "error": b.error, "critical": b.critical, "unknown": b.unknown,
        "distinct_groups": b.distinct_groups, "new_groups": b.new_groups,
    }
    metrics = getattr(b, "metrics", None)
    if metrics:
        d["metrics"] = dict(metrics)
    return d


def _json_safe(obj: Any) -> Any:
    """Recursively convert dataclasses / datetimes to JSON-compatible data."""
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: _json_safe(getattr(obj, f.name)) for f in dataclasses.fields(obj)}
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {str(k): _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    return obj


def _event_row(import_id: int, ev: LogEvent, fp: str | None) -> dict:
    return {
        "import_id": import_id, "event_id": ev.event_id, "line_no": ev.line_no, "ts_ms": to_ms(ev.timestamp),
        "level": int(ev.level), "service": ev.service, "logger": ev.logger, "message": ev.message,
        "stack_trace": ev.stack_trace, "fields": ev.fields, "metrics": ev.metrics, "fingerprint": fp,
    }


def ingest(
    factory: sessionmaker[Session],
    stream: BinaryIO,
    *,
    filename: str,
    size_bytes: int,
    file_sha256: str,
    format: str | None = None,
    default_service: str | None = None,
    limits: Limits = DEFAULT_LIMITS,
    detector=None,
) -> IngestResult:
    """Parse + analyze + persist in ONE transaction; re-importing the same bytes and
    options returns the existing run without writing anything.

    Raises UnsupportedFormatError (before any write) for unusable input.
    """
    key = content_key(file_sha256, format, default_service)
    with factory() as s:
        existing = s.scalar(select(Import.id).where(Import.content_key == key))
        if existing is not None:
            return IngestResult(existing, True)

    session = parse(stream, filename=filename, format=format, default_service=default_service, limits=limits)

    try:
        with factory.begin() as s:
            imp = Import(
                content_key=key, filename=filename[:255], format=session.format, default_service=default_service,
                size_bytes=size_bytes, created_ms=now_ms(), event_count=0, window_seconds=60,
                level_counts={}, service_counts={}, diagnostics={}, timeline=[], anomaly=None, notes=[],
            )
            s.add(imp)
            s.flush()
            import_id = imp.id
            buf: list[dict] = []

            def observe(ev: LogEvent, fp: str | None) -> None:
                buf.append(_event_row(import_id, ev, fp))
                if len(buf) >= _CHUNK:
                    s.execute(insert(Event), buf)
                    buf.clear()

            result = analyze(session, observer=observe)
            if buf:
                s.execute(insert(Event), buf)
            _fill_import(s, imp, result, session.diagnostics.to_dict(), detector)
            s.execute(insert(Group), _group_rows(import_id, result))
            return IngestResult(import_id, False)
    except IntegrityError:
        # a concurrent request stored the same run first
        with factory() as s:
            existing = s.scalar(select(Import.id).where(Import.content_key == key))
        if existing is None:
            raise
        return IngestResult(existing, True)


def _fill_import(s: Session, imp: Import, res: Analysis, diagnostics: dict, detector) -> None:
    imp.event_count = res.total_events
    imp.first_event_ms = to_ms(res.first_event)
    imp.last_event_ms = to_ms(res.last_event)
    imp.window_seconds = res.timeline.window_seconds
    imp.level_counts = res.level_counts
    imp.service_counts = dict(Counter(res.service_counts).most_common(50))
    imp.diagnostics = diagnostics
    imp.timeline = [_bucket_dict(b) for b in res.timeline.buckets]
    imp.notes = list(res.notes)
    if detector is not None and res.timeline.buckets:
        try:
            imp.anomaly = _json_safe(detector(res.timeline))
        except Exception as e:  # noqa: BLE001 - detection is advisory; never fail an import for it
            imp.anomaly = {"error": f"anomaly detection failed: {type(e).__name__}"}


def _group_rows(import_id: int, res: Analysis) -> list[dict]:
    w = res.timeline.window_seconds
    rows = []
    for g in res.groups:
        a = res.activity[g.fingerprint]
        binned: Counter[int] = Counter()
        for sec, n in g.series.items():
            binned[sec - sec % w] += n
        rows.append({
            "import_id": import_id, "fingerprint": g.fingerprint, "title": g.title, "service": g.service,
            "exc_type": g.exc_type, "kind": g.kind, "frame": g.frame, "max_level": int(g.max_level),
            "count": g.count, "untimed_count": g.untimed_count, "first_seen_ms": to_ms(g.first_seen),
            "last_seen_ms": to_ms(g.last_seen), "sample_event_id": g.sample_event_id,
            "sample_message": g.sample_message, "sample_stack_trace": g.sample_stack_trace,
            "level_counts": g.level_counts,
            "activity": {
                "active_windows": a.active_windows, "peak_window_start_ms": to_ms(a.peak_window_start),
                "peak_window_count": a.peak_window_count, "rate_per_minute": a.rate_per_minute,
                "recurrence": a.recurrence, "period_seconds": a.period_seconds, "status": a.status,
            },
            "windows": [[sec * 1000, n] for sec, n in sorted(binned.items())],
        })
    return rows


# ------------------------------------------------------------------- queries
def _like(term: str) -> str:
    return "%" + term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def effective_status():
    return func.coalesce(Triage.status, "open")


def list_incidents(
    s: Session, import_id: int, *, q: str | None = None, min_level: int | None = None,
    service: str | None = None, status: str | None = None, ongoing: bool | None = None,
    sort: str = "count", order: str = "desc", limit: int = 50, offset: int = 0,
) -> tuple[list[tuple[Group, str]], int]:
    stmt = (
        select(Group, effective_status().label("status"))
        .outerjoin(Triage, Triage.fingerprint == Group.fingerprint)
        .where(Group.import_id == import_id)
    )
    if q:
        pat = _like(q)
        stmt = stmt.where(or_(
            Group.title.like(pat, escape="\\"), Group.sample_message.like(pat, escape="\\"),
            Group.exc_type.like(pat, escape="\\"), Group.frame.like(pat, escape="\\"),
        ))
    if min_level is not None:
        stmt = stmt.where(Group.max_level >= min_level)
    if service:
        stmt = stmt.where(Group.service == service)
    if status:
        stmt = stmt.where(effective_status() == status)
    if ongoing is not None:
        status_json = func.json_extract(Group.activity, "$.status")
        stmt = stmt.where(status_json == "ongoing") if ongoing else stmt.where(status_json != "ongoing")
    total = s.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0
    cols = {"count": Group.count, "first_seen": Group.first_seen_ms, "last_seen": Group.last_seen_ms,
            "level": Group.max_level}
    col = cols.get(sort, Group.count)
    stmt = stmt.order_by(col.asc() if order == "asc" else col.desc(), Group.fingerprint)
    return [(g, st) for g, st in s.execute(stmt.limit(limit).offset(offset)).all()], total


def get_group(s: Session, import_id: int, fingerprint: str) -> tuple[Group, str] | None:
    row = s.execute(
        select(Group, effective_status())
        .outerjoin(Triage, Triage.fingerprint == Group.fingerprint)
        .where(Group.import_id == import_id, Group.fingerprint == fingerprint)
    ).first()
    return (row[0], row[1]) if row else None


def related_groups(s: Session, import_id: int, g: Group, window_ms: int, limit: int = 10) -> list[Group]:
    """Other groups whose first occurrence is within `window_ms` of this group's first occurrence."""
    if g.first_seen_ms is None:
        return []
    stmt = (
        select(Group)
        .where(Group.import_id == import_id, Group.fingerprint != g.fingerprint,
               Group.first_seen_ms.between(g.first_seen_ms - window_ms, g.first_seen_ms + window_ms))
        .order_by(func.abs(Group.first_seen_ms - g.first_seen_ms), Group.fingerprint)
        .limit(limit)
    )
    return list(s.scalars(stmt))


def list_events(
    s: Session, import_id: int, *, fingerprint: str | None = None, q: str | None = None,
    min_level: int | None = None, start_ms: int | None = None, end_ms: int | None = None,
    limit: int = 50, offset: int = 0,
) -> tuple[list[Event], int]:
    stmt = select(Event).where(Event.import_id == import_id)
    if fingerprint:
        stmt = stmt.where(Event.fingerprint == fingerprint)
    if q:
        pat = _like(q)
        stmt = stmt.where(or_(Event.message.like(pat, escape="\\"), Event.stack_trace.like(pat, escape="\\")))
    if min_level is not None:
        stmt = stmt.where(Event.level >= min_level)
    if start_ms is not None:
        stmt = stmt.where(Event.ts_ms >= start_ms)
    if end_ms is not None:
        stmt = stmt.where(Event.ts_ms < end_ms)
    total = s.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    stmt = stmt.order_by(case((Event.ts_ms.is_(None), 1), else_=0), Event.ts_ms, Event.line_no).limit(limit).offset(offset)
    return list(s.scalars(stmt)), total


def set_status(s: Session, fingerprint: str, status: str) -> None:
    if status not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES}")
    ts = now_ms()
    updated = s.execute(update(Triage).where(Triage.fingerprint == fingerprint).values(status=status, updated_ms=ts))
    if updated.rowcount == 0:
        s.add(Triage(fingerprint=fingerprint, status=status, updated_ms=ts))


def fingerprint_exists(s: Session, fingerprint: str) -> bool:
    return s.scalar(select(func.count()).select_from(Group).where(Group.fingerprint == fingerprint)) > 0


def add_note(s: Session, fingerprint: str, body: str) -> Note:
    n = Note(fingerprint=fingerprint, body=body, created_ms=now_ms())
    s.add(n)
    s.flush()
    return n


def list_notes(s: Session, fingerprint: str) -> list[Note]:
    return list(s.scalars(select(Note).where(Note.fingerprint == fingerprint).order_by(Note.created_ms, Note.id)))


def delete_note(s: Session, note_id: int) -> bool:
    return s.execute(delete(Note).where(Note.id == note_id)).rowcount > 0


def delete_import(s: Session, import_id: int) -> bool:
    return s.execute(delete(Import).where(Import.id == import_id)).rowcount > 0


def overview_totals(s: Session) -> dict:
    return {
        "imports": s.scalar(select(func.count()).select_from(Import)) or 0,
        "events": s.scalar(select(func.coalesce(func.sum(Import.event_count), 0))) or 0,
        "groups": s.scalar(select(func.count()).select_from(Group)) or 0,
    }

