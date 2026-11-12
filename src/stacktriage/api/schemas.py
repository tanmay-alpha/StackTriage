"""Pydantic response/request models."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any, Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from ..models import Level

T = TypeVar("T")
Status = Literal["open", "investigating", "resolved", "ignored"]


def dt(ms: int | None) -> datetime | None:
    return None if ms is None else datetime.fromtimestamp(ms / 1000, tz=UTC)


def level_name(v: int) -> str:
    try:
        return Level(v).name
    except ValueError:
        return "UNKNOWN"


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    limit: int
    offset: int


class ImportSummary(BaseModel):
    id: int
    filename: str
    format: str
    default_service: str | None
    size_bytes: int
    created_at: datetime
    event_count: int
    incident_count: int
    first_event: datetime | None
    last_event: datetime | None
    window_seconds: int
    truncated: bool
    duplicate: bool = False


class ImportDetail(ImportSummary):
    level_counts: dict[str, int]
    service_counts: dict[str, int]
    diagnostics: dict[str, Any]
    timeline: list[dict[str, Any]]
    anomaly: dict[str, Any] | None
    notes: list[str]


class Activity(BaseModel):
    state: Literal["ongoing", "ended", "unknown"]
    recurrence: str
    period_seconds: float | None
    rate_per_minute: float | None
    active_windows: int
    peak_window_start: datetime | None
    peak_window_count: int


class IncidentItem(BaseModel):
    fingerprint: str
    title: str
    service: str
    exc_type: str | None
    frame: str | None
    max_level: str
    count: int
    first_seen: datetime | None
    last_seen: datetime | None
    status: Status
    activity: Activity


class NoteOut(BaseModel):
    id: int
    body: str
    created_at: datetime


class WindowCount(BaseModel):
    start: datetime
    count: int


class IncidentDetail(IncidentItem):
    kind: str
    untimed_count: int
    sample_event_id: str
    sample_message: str
    sample_stack_trace: str | None
    level_counts: dict[str, int]
    window_seconds: int
    windows: list[WindowCount]
    notes: list[NoteOut]
    related: list[IncidentItem]


class EventOut(BaseModel):
    event_id: str
    line_no: int
    timestamp: datetime | None
    level: str
    service: str | None
    logger: str | None
    message: str
    stack_trace: str | None
    fields: dict[str, str]
    metrics: dict[str, float]
    fingerprint: str | None


class StatusIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Status


class NoteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    body: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=5000)]


class OverviewOut(BaseModel):
    imports: int
    events: int
    groups: int
    latest_import: ImportSummary | None
    status_counts: dict[str, int] = Field(default_factory=dict)


# ------------------------------------------------------------- comparison
class CompareSide(ImportSummary):
    duration_seconds: float | None = Field(description="last_event - first_event; None without timestamps")
    timed_events: int
    untimed_events: int
    services: dict[str, int]
    time_valid: bool = Field(description="usable for per-minute rates")


class CompareRates(BaseModel):
    count: int
    timed_count: int
    per_1k_events: float
    per_minute: float | None
    first_seen: datetime | None
    last_seen: datetime | None
    max_level: str


class CompareItem(BaseModel):
    fingerprint: str
    title: str
    service: str
    exc_type: str | None
    frame: str | None
    category: Literal["new", "disappeared", "recurring"]
    change: Literal["increased", "decreased", "no_clear_change", "insufficient_data"] | None
    status: Status
    status_updated_at: datetime | None
    resolved_observed: bool = Field(description="marked resolved and present in the target capture")
    baseline: CompareRates | None
    target: CompareRates | None
    rate_ratio: float | None = Field(description="target rate / baseline rate on the chosen basis")
    percent_change: float | None
    p_value: float | None
    q_value: float | None = Field(description="Benjamini-Hochberg adjusted p-value")
    evidence: Literal["significant", "weak", "low_sample"]
    bases_disagree: bool
    investigate_import_id: int = Field(description="import whose investigation page shows this group")


class CompareWarning(BaseModel):
    code: str
    message: str


class CompareParameters(BaseModel):
    alpha: float
    min_fold_change: float
    min_occurrences: int
    min_coverage_seconds: int


class CompareOut(BaseModel):
    baseline: CompareSide
    target: CompareSide
    basis: Literal["events", "time"]
    time_comparable: bool
    parameters: CompareParameters
    warnings: list[CompareWarning]
    totals: dict[str, int]
    items: list[CompareItem]
    total: int
    limit: int
    offset: int
