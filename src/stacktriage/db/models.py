"""SQLAlchemy schema. All timestamps are UTC epoch milliseconds (INTEGER)."""
from __future__ import annotations

from sqlalchemy import JSON, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Import(Base):
    """One analysis run over one uploaded file.

    `content_key` = sha256(file bytes + parse options). It is UNIQUE, so importing
    the same file with the same options twice cannot create a second run.
    """

    __tablename__ = "imports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    content_key: Mapped[str] = mapped_column(String(64), unique=True)
    filename: Mapped[str] = mapped_column(String(255))
    format: Mapped[str] = mapped_column(String(16))
    default_service: Mapped[str | None] = mapped_column(String(100), nullable=True)
    size_bytes: Mapped[int] = mapped_column(Integer)
    created_ms: Mapped[int] = mapped_column(Integer)
    event_count: Mapped[int] = mapped_column(Integer)
    first_event_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_event_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    window_seconds: Mapped[int] = mapped_column(Integer)
    level_counts: Mapped[dict] = mapped_column(JSON)
    service_counts: Mapped[dict] = mapped_column(JSON)
    diagnostics: Mapped[dict] = mapped_column(JSON)
    timeline: Mapped[list] = mapped_column(JSON)  # list of bucket dicts (real counts only)
    anomaly: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    notes: Mapped[list] = mapped_column(JSON)


class Group(Base):
    """An error group (incident candidate) within one import."""

    __tablename__ = "groups"

    import_id: Mapped[int] = mapped_column(ForeignKey("imports.id", ondelete="CASCADE"), primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(16), primary_key=True)
    title: Mapped[str] = mapped_column(Text)
    service: Mapped[str] = mapped_column(String(100))
    exc_type: Mapped[str | None] = mapped_column(String(200), nullable=True)
    kind: Mapped[str] = mapped_column(String(16))
    frame: Mapped[str | None] = mapped_column(String(300), nullable=True)
    max_level: Mapped[int] = mapped_column(Integer)
    count: Mapped[int] = mapped_column(Integer)
    untimed_count: Mapped[int] = mapped_column(Integer)
    first_seen_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_seen_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sample_event_id: Mapped[str] = mapped_column(String(32))
    sample_message: Mapped[str] = mapped_column(Text)
    sample_stack_trace: Mapped[str | None] = mapped_column(Text, nullable=True)
    level_counts: Mapped[dict] = mapped_column(JSON)
    activity: Mapped[dict] = mapped_column(JSON)  # recurrence, status, rate, peak window
    windows: Mapped[list] = mapped_column(JSON)  # [[window_start_ms, count], ...]

    __table_args__ = (
        Index("ix_groups_import_count", "import_id", "count"),
        Index("ix_groups_import_first", "import_id", "first_seen_ms"),
    )


class Event(Base):
    __tablename__ = "events"

    import_id: Mapped[int] = mapped_column(ForeignKey("imports.id", ondelete="CASCADE"), primary_key=True)
    event_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    line_no: Mapped[int] = mapped_column(Integer)
    ts_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    level: Mapped[int] = mapped_column(Integer)
    service: Mapped[str | None] = mapped_column(String(100), nullable=True)
    logger: Mapped[str | None] = mapped_column(String(200), nullable=True)
    message: Mapped[str] = mapped_column(Text)
    stack_trace: Mapped[str | None] = mapped_column(Text, nullable=True)
    fields: Mapped[dict] = mapped_column(JSON)
    metrics: Mapped[dict] = mapped_column(JSON)
    fingerprint: Mapped[str | None] = mapped_column(String(16), nullable=True)

    __table_args__ = (
        Index("ix_events_import_fp_ts", "import_id", "fingerprint", "ts_ms"),
        Index("ix_events_import_ts", "import_id", "ts_ms"),
        Index("ix_events_import_level", "import_id", "level"),
    )


class Triage(Base):
    """Review status per error fingerprint; carries over between imports."""

    __tablename__ = "triage"

    fingerprint: Mapped[str] = mapped_column(String(16), primary_key=True)
    status: Mapped[str] = mapped_column(String(16))  # open | investigating | resolved | ignored
    updated_ms: Mapped[int] = mapped_column(Integer)


class Note(Base):
    __tablename__ = "notes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(16), index=True)
    body: Mapped[str] = mapped_column(Text)
    created_ms: Mapped[int] = mapped_column(Integer)


STATUSES = ("open", "investigating", "resolved", "ignored")
