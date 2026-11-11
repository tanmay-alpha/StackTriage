"""FastAPI application factory."""
import hashlib
import hmac
import logging
import os
import re
import tempfile
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi import Path as PathParam
from fastapi.concurrency import run_in_threadpool
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from ..db import make_engine, migrate
from ..db import store
from ..db.models import Group, Import
from ..models import Limits, UnsupportedFormatError
from ..parsers import FORMATS
from . import export, schemas
from .settings import Settings

log = logging.getLogger("stacktriage")
_SAFE_STATIC = re.compile(r"^[A-Za-z0-9_@.\-]+(?:/[A-Za-z0-9_@.\-]+)*$")
_SERVICE_RE = re.compile(r"^[\w .:/@-]{1,100}$")
ImportId = Annotated[int, PathParam(ge=1, le=2**31 - 1)]
NoteId = Annotated[int, PathParam(ge=1, le=2**31 - 1)]
LEVELS = {"debug": 10, "info": 20, "warning": 30, "error": 40, "critical": 50}


def _anomaly_detector():
    """Anomaly detection is optional: absent => imports store no anomaly report."""
    try:
        from ..anomaly import detect_anomalies  # type: ignore[import-not-found]
    except ImportError:
        return None
    return lambda timeline: detect_anomalies(timeline)


def create_app(settings: Settings | None = None) -> FastAPI:
    cfg = settings or Settings.from_env()
    cfg.validate()
    if cfg.db_path != ":memory:":
        Path(cfg.db_path).parent.mkdir(parents=True, exist_ok=True)
    engine = make_engine(cfg.db_path)
    migrate(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    detector = _anomaly_detector()

    dev_docs = cfg.env == "development" and cfg.api_key is None
    app = FastAPI(title="StackTriage", version="0.1.0", docs_url="/api/docs" if dev_docs else None,
                  redoc_url=None, openapi_url="/api/openapi.json" if dev_docs else None)
    app.state.factory = factory
    app.state.engine = engine
    if cfg.api_key is None:
        log.warning("STACKTRIAGE_API_KEY not set: API is unauthenticated (local use only)")

    hosts = cfg.effective_allowed_hosts()
    if hosts:
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(hosts))
    if cfg.cors_origins:
        app.add_middleware(CORSMiddleware, allow_origins=list(cfg.cors_origins), allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
                           allow_headers=["Authorization", "X-API-Key", "Content-Type"])

    @app.middleware("http")
    async def security_headers(request: Request, call_next):  # noqa: ANN001
        resp = await call_next(request)
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "no-referrer"
        resp.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
            "script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        )
        if request.url.path.startswith("/api/"):
            resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception) -> JSONResponse:
        # never log request bodies or headers: they may hold credentials or log content
        log.error("unhandled %s on %s %s", type(exc).__name__, request.method, request.url.path)
        return JSONResponse({"detail": "internal server error"}, status_code=500)

    @app.exception_handler(RequestValidationError)
    async def validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        errs = [{"loc": list(e.get("loc", [])), "msg": e.get("msg", "invalid")} for e in exc.errors()]
        return JSONResponse({"detail": errs}, status_code=422)

    # ------------------------------------------------------------------ auth
    def require_auth(request: Request) -> None:
        if cfg.api_key is None:
            return
        supplied = request.headers.get("x-api-key")
        auth = request.headers.get("authorization", "")
        if supplied is None and auth[:7].lower() == "bearer ":
            supplied = auth[7:].strip()
        if supplied is None or not hmac.compare_digest(supplied.encode(), cfg.api_key.encode()):
            raise HTTPException(401, "missing or invalid API key", headers={"WWW-Authenticate": "Bearer"})

    def get_db() -> Iterator[Session]:
        with factory() as s:
            try:
                yield s
                s.commit()
            except BaseException:
                s.rollback()
                raise

    DB = Annotated[Session, Depends(get_db)]
    api = APIRouter(prefix="/api", dependencies=[Depends(require_auth)])

    @app.get("/api/health", include_in_schema=False)
    def health() -> dict:
        with factory() as s:
            s.execute(select(1))
        return {"status": "ok", "auth_required": cfg.api_key is not None}

    # ------------------------------------------------------------- helpers
    def summary(s: Session, imp: Import, duplicate: bool = False) -> dict:
        n = s.scalar(select(func.count()).select_from(Group).where(Group.import_id == imp.id)) or 0
        return dict(
            id=imp.id, filename=imp.filename, format=imp.format, default_service=imp.default_service,
            size_bytes=imp.size_bytes, created_at=schemas.dt(imp.created_ms), event_count=imp.event_count,
            incident_count=n, first_event=schemas.dt(imp.first_event_ms), last_event=schemas.dt(imp.last_event_ms),
            window_seconds=imp.window_seconds, truncated=bool(imp.diagnostics.get("truncated")), duplicate=duplicate,
        )

    def get_import(s: Session, import_id: ImportId) -> Import:
        imp = s.get(Import, import_id)
        if imp is None:
            raise HTTPException(404, "import not found")
        return imp

    def incident_item(g: Group, status: str) -> schemas.IncidentItem:
        a = g.activity
        return schemas.IncidentItem(
            fingerprint=g.fingerprint, title=g.title, service=g.service, exc_type=g.exc_type, frame=g.frame,
            max_level=schemas.level_name(g.max_level), count=g.count, first_seen=schemas.dt(g.first_seen_ms),
            last_seen=schemas.dt(g.last_seen_ms), status=status,  # type: ignore[arg-type]
            activity=schemas.Activity(
                state=a.get("status", "unknown"), recurrence=a.get("recurrence", "single"),
                period_seconds=a.get("period_seconds"), rate_per_minute=a.get("rate_per_minute"),
                active_windows=a.get("active_windows", 0), peak_window_start=schemas.dt(a.get("peak_window_start_ms")),
                peak_window_count=a.get("peak_window_count", 0)),
        )

    def event_out(e) -> schemas.EventOut:  # noqa: ANN001
        return schemas.EventOut(
            event_id=e.event_id, line_no=e.line_no, timestamp=schemas.dt(e.ts_ms), level=schemas.level_name(e.level),
            service=e.service, logger=e.logger, message=e.message, stack_trace=e.stack_trace, fields=e.fields,
            metrics=e.metrics, fingerprint=e.fingerprint)

    Limit = Annotated[int, Query(ge=1, le=200)]
    Offset = Annotated[int, Query(ge=0, le=1_000_000)]

    def parse_level(v: str | None) -> int | None:
        if v is None:
            return None
        if v.lower() not in LEVELS:
            raise HTTPException(422, f"level must be one of {sorted(LEVELS)}")
        return LEVELS[v.lower()]

    # -------------------------------------------------------------- imports
    @api.post("/imports", response_model=schemas.ImportSummary, status_code=201)
    async def create_import(
        request: Request, response: Response, filename: Annotated[str, Query(min_length=1, max_length=255)],
        service: Annotated[str | None, Query(max_length=100)] = None, format: str | None = None,
    ):
        """Upload a log file as the raw request body (streamed, size-capped)."""
        if format is not None and format not in FORMATS:
            raise HTTPException(422, f"format must be one of {list(FORMATS)}")
        if service is not None and not _SERVICE_RE.match(service):
            raise HTTPException(422, "service contains unsupported characters")
        # Only application/octet-stream: browsers cannot send it cross-origin without a CORS preflight,
        # so a malicious web page cannot upload to a local unauthenticated instance.
        if request.headers.get("content-type", "").split(";")[0].strip().lower() != "application/octet-stream":
            raise HTTPException(415, "Content-Type must be application/octet-stream")
        declared = request.headers.get("content-length")
        if declared is not None and declared.isdigit() and int(declared) > cfg.max_upload_bytes:
            raise HTTPException(413, f"file exceeds the {cfg.max_upload_bytes} byte upload limit")
        safe_name = os.path.basename(filename.replace("\\", "/")) or "upload"
        sha, size = hashlib.sha256(), 0
        with tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024) as tmp:
            async for chunk in request.stream():
                size += len(chunk)
                if size > cfg.max_upload_bytes:
                    raise HTTPException(413, f"file exceeds the {cfg.max_upload_bytes} byte upload limit")
                sha.update(chunk)
                tmp.write(chunk)
            if size == 0:
                raise HTTPException(400, "empty upload")
            tmp.seek(0)
            try:
                result = await run_in_threadpool(
                    store.ingest, factory, tmp, filename=safe_name, size_bytes=size, file_sha256=sha.hexdigest(),
                    format=format, default_service=service,
                    limits=Limits(max_bytes=cfg.max_upload_bytes, max_events=cfg.max_events), detector=detector)
            except UnsupportedFormatError as e:
                raise HTTPException(415, str(e)) from e
        with factory() as s:
            out = summary(s, s.get(Import, result.import_id), result.duplicate)
        if result.duplicate:
            response.status_code = 200
        return out

    @api.get("/imports", response_model=schemas.Page[schemas.ImportSummary])
    def list_imports(db: DB, limit: Limit = 50, offset: Offset = 0):
        total = db.scalar(select(func.count()).select_from(Import)) or 0
        rows = db.scalars(select(Import).order_by(Import.created_ms.desc(), Import.id.desc()).limit(limit).offset(offset))
        return {"items": [summary(db, i) for i in rows], "total": total, "limit": limit, "offset": offset}

    @api.get("/imports/{import_id}", response_model=schemas.ImportDetail)
    def import_detail(import_id: ImportId, db: DB):
        imp = get_import(db, import_id)
        d = summary(db, imp)
        d.update(level_counts=imp.level_counts, service_counts=imp.service_counts, diagnostics=imp.diagnostics,
                 timeline=imp.timeline, anomaly=imp.anomaly, notes=imp.notes)
        return d

    @api.delete("/imports/{import_id}", status_code=204)
    def remove_import(import_id: ImportId, db: DB):
        if not store.delete_import(db, import_id):
            raise HTTPException(404, "import not found")
        return Response(status_code=204)

    # ------------------------------------------------------------ incidents
    @api.get("/imports/{import_id}/incidents", response_model=schemas.Page[schemas.IncidentItem])
    def incidents(
        import_id: ImportId, db: DB, q: Annotated[str | None, Query(max_length=200)] = None, level: str | None = None,
        service: Annotated[str | None, Query(max_length=100)] = None, status: schemas.Status | None = None,
        ongoing: bool | None = None, sort: Annotated[str, Query(pattern="^(count|first_seen|last_seen|level)$")] = "count",
        order: Annotated[str, Query(pattern="^(asc|desc)$")] = "desc", limit: Limit = 50, offset: Offset = 0,
    ):
        get_import(db, import_id)
        rows, total = store.list_incidents(db, import_id, q=q, min_level=parse_level(level), service=service,
                                           status=status, ongoing=ongoing, sort=sort, order=order, limit=limit, offset=offset)
        return {"items": [incident_item(g, st) for g, st in rows], "total": total, "limit": limit, "offset": offset}

    @api.get("/imports/{import_id}/incidents/{fingerprint}", response_model=schemas.IncidentDetail)
    def incident_detail(import_id: ImportId, fingerprint: str, db: DB):
        row = store.get_group(db, import_id, fingerprint)
        if row is None:
            raise HTTPException(404, "incident not found")
        g, st = row
        imp = get_import(db, import_id)
        related = store.related_groups(db, import_id, g, window_ms=max(imp.window_seconds, 60) * 1000 * 3)
        statuses = dict(db.execute(select(store.Triage.fingerprint, store.Triage.status)
                                   .where(store.Triage.fingerprint.in_([r.fingerprint for r in related] or [""]))).all())
        base = incident_item(g, st).model_dump()
        return schemas.IncidentDetail(
            **base, kind=g.kind, untimed_count=g.untimed_count, sample_event_id=g.sample_event_id,
            sample_message=g.sample_message, sample_stack_trace=g.sample_stack_trace, level_counts=g.level_counts,
            window_seconds=imp.window_seconds,
            windows=[schemas.WindowCount(start=schemas.dt(ms), count=n) for ms, n in g.windows],  # type: ignore[arg-type]
            notes=[schemas.NoteOut(id=n.id, body=n.body, created_at=schemas.dt(n.created_ms)) for n in store.list_notes(db, fingerprint)],  # type: ignore[arg-type]
            related=[incident_item(r, statuses.get(r.fingerprint, "open")) for r in related])

    @api.get("/imports/{import_id}/incidents/{fingerprint}/events", response_model=schemas.Page[schemas.EventOut])
    def incident_events(import_id: ImportId, fingerprint: str, db: DB, limit: Limit = 20, offset: Offset = 0):
        if store.get_group(db, import_id, fingerprint) is None:
            raise HTTPException(404, "incident not found")
        rows, total = store.list_events(db, import_id, fingerprint=fingerprint, limit=limit, offset=offset)
        return {"items": [event_out(e) for e in rows], "total": total, "limit": limit, "offset": offset}

    @api.get("/imports/{import_id}/events", response_model=schemas.Page[schemas.EventOut])
    def events(
        import_id: ImportId, db: DB, q: Annotated[str | None, Query(max_length=200)] = None, level: str | None = None,
        start: datetime | None = None, end: datetime | None = None, limit: Limit = 50, offset: Offset = 0,
    ):
        get_import(db, import_id)
        to_ms = lambda d: None if d is None else store.to_ms(d if d.tzinfo else d.replace(tzinfo=UTC))  # noqa: E731
        rows, total = store.list_events(db, import_id, q=q, min_level=parse_level(level), start_ms=to_ms(start),
                                        end_ms=to_ms(end), limit=limit, offset=offset)
        return {"items": [event_out(e) for e in rows], "total": total, "limit": limit, "offset": offset}

    # ------------------------------------------------------- review + notes
    def require_fingerprint(db: Session, fingerprint: str) -> None:
        if not store.fingerprint_exists(db, fingerprint):
            raise HTTPException(404, "unknown incident fingerprint")

    @api.put("/incidents/{fingerprint}/status", response_model=schemas.StatusIn)
    def put_status(fingerprint: str, body: schemas.StatusIn, db: DB):
        require_fingerprint(db, fingerprint)
        store.set_status(db, fingerprint, body.status)
        return body

    @api.post("/incidents/{fingerprint}/notes", response_model=schemas.NoteOut, status_code=201)
    def post_note(fingerprint: str, body: schemas.NoteIn, db: DB):
        require_fingerprint(db, fingerprint)
        n = store.add_note(db, fingerprint, body.body)
        return schemas.NoteOut(id=n.id, body=n.body, created_at=schemas.dt(n.created_ms))  # type: ignore[arg-type]

    @api.delete("/incidents/{fingerprint}/notes/{note_id}", status_code=204)
    def remove_note(fingerprint: str, note_id: NoteId, db: DB):
        notes = {n.id for n in store.list_notes(db, fingerprint)}
        if note_id not in notes or not store.delete_note(db, note_id):
            raise HTTPException(404, "note not found")
        return Response(status_code=204)

    # --------------------------------------------------------------- export
    @api.get("/imports/{import_id}/export")
    def export_import(import_id: ImportId, db: DB, format: Annotated[str, Query(pattern="^(csv|json|md)$")] = "csv"):
        imp = get_import(db, import_id)
        rows, _ = store.list_incidents(db, import_id, limit=10_000, sort="count")
        name = export.safe_filename(f"stacktriage-{imp.id}-{imp.filename}")
        hdr = {"Content-Disposition": f'attachment; filename="{name}.{format}"'}
        if format == "json":
            body = schemas.Page[schemas.IncidentItem](
                items=[incident_item(g, st) for g, st in rows], total=len(rows), limit=len(rows), offset=0)
            payload = {"import": summary(db, imp), "anomaly": imp.anomaly,
                       "incidents": body.model_dump(mode="json")["items"]}
            return JSONResponse(jsonable_encoder(payload), headers=hdr)
        if format == "csv":
            header = ["status", "level", "service", "count", "first_seen", "last_seen", "state", "recurrence",
                      "exception", "frame", "title", "fingerprint", "sample_message"]
            data = [[st, schemas.level_name(g.max_level), g.service, g.count, schemas.dt(g.first_seen_ms),
                     schemas.dt(g.last_seen_ms), g.activity.get("status"), g.activity.get("recurrence"),
                     g.exc_type or "", g.frame or "", g.title, g.fingerprint, g.sample_message] for g, st in rows]
            return PlainTextResponse(export.to_csv(header, data), media_type="text/csv; charset=utf-8", headers=hdr)
        return PlainTextResponse(_markdown_report(imp, rows), media_type="text/markdown; charset=utf-8", headers=hdr)

    @api.get("/overview", response_model=schemas.OverviewOut)
    def overview(db: DB):
        totals = store.overview_totals(db)
        latest = db.scalars(select(Import).order_by(Import.created_ms.desc(), Import.id.desc()).limit(1)).first()
        return {**totals, "latest_import": summary(db, latest) if latest else None}

    app.include_router(api)
    _mount_static(app, cfg)
    return app


def _markdown_report(imp: Import, rows: list) -> str:
    md = export.md_escape
    fmt = lambda ms: "-" if ms is None else datetime.fromtimestamp(ms / 1000, tz=UTC).strftime("%Y-%m-%d %H:%M:%S UTC")  # noqa: E731
    out = [f"# StackTriage report: {md(imp.filename)}", "",
           f"- Format: {imp.format}", f"- Events: {imp.event_count}",
           f"- Range: {fmt(imp.first_event_ms)} to {fmt(imp.last_event_ms)}",
           f"- Error groups: {len(rows)}", ""]
    an = imp.anomaly or {}
    eps = an.get("episodes") or []
    if eps:
        out += ["## Unusual periods", "",
                "Statistical findings: windows whose activity differed from the baseline. They are not causes.", "",
                "| Start | End | Classification | Severity | Peak score |", "|---|---|---|---|---|"]
        for e in eps:
            out.append(f"| {md(str(e.get('start')))} | {md(str(e.get('end')))} | {md(str(e.get('classification')))} | "
                       f"{md(str(e.get('severity')))} | {e.get('peak_score', 0):.2f} |")
        out.append("")
    out += ["## Error groups", "", "| Status | Level | Count | First seen | Last seen | State | Title |", "|---|---|---|---|---|---|---|"]
    for g, st in rows:
        out.append(f"| {st} | {schemas.level_name(g.max_level)} | {g.count} | {fmt(g.first_seen_ms)} | {fmt(g.last_seen_ms)} | "
                   f"{g.activity.get('status', '-')} | {md(g.title)} |")
    return "\n".join(out) + "\n"


def _mount_static(app: FastAPI, cfg: Settings) -> None:
    if not cfg.static_dir:
        return
    root = Path(cfg.static_dir).resolve()
    index = root / "index.html"
    if not index.exists():
        return

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        if path.startswith("api/"):
            raise HTTPException(404, "not found")
        # only plain relative paths are looked up on disk (no backslashes, drive letters, NUL, "..")
        if path and _SAFE_STATIC.match(path) and ".." not in path.split("/"):
            candidate = (root / path).resolve()
            if candidate.is_file() and root in candidate.parents:
                return FileResponse(candidate)
        return FileResponse(index)


def app_factory() -> FastAPI:  # for `uvicorn stacktriage.api.app:app_factory --factory`
    return create_app()

