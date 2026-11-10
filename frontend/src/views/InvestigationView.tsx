import { useEffect, useId, useMemo, useState } from "react";
import type { FormEvent } from "react";
import { Link, useLocation, useParams } from "react-router-dom";
import { ApiError, api } from "../api/client";
import type { EventOut, ImportDetail, IncidentDetail, IncidentItem, NoteOut, Page } from "../api/types";
import { episodeBands } from "../components/Anomaly";
import { ExportButtons, StatusSelect } from "../components/IncidentControls";
import { CopyButton, StackTrace } from "../components/StackTrace";
import { TimelineChart } from "../components/TimelineChart";
import type { ChartBucket, TooltipContent } from "../components/TimelineChart";
import { anomalyAt, stepOf } from "../components/VolumeChart";
import { ActivityBadge, Button, ConfirmDialog, EmptyState, ErrorState, LevelBadge, LinkButton, LoadingBlock, Pagination, Panel, Skeleton } from "../components/ui";
import { classificationLabel, fmtDuration, fmtInt, fmtNum, fmtRate, recurrenceLabel, toMs } from "../lib/format";
import { forgetImport, rememberImport } from "../lib/lastImport";
import { useAsync } from "../lib/useAsync";
import { useTime, useToast } from "../state/app";
import { useDocTitle } from "./common";

const EVENT_PAGE = 10;

export function InvestigationView() {
  const { importId: idParam = "", fingerprint = "" } = useParams();
  const importId = Number(idParam);
  const loc = useLocation();
  const backSearch = typeof loc.state === "object" && loc.state !== null && "search" in loc.state && typeof loc.state.search === "string" ? loc.state.search : "";

  useEffect(() => {
    if (Number.isFinite(importId)) rememberImport(importId);
  }, [importId]);

  const detail = useAsync<IncidentDetail>(`inc:${importId}:${fingerprint}`, (s) => api.get<IncidentDetail>(`/imports/${importId}/incidents/${fingerprint}`, undefined, s));
  const imp = useAsync<ImportDetail>(`import:${importId}`, (s) => api.get<ImportDetail>(`/imports/${importId}`, undefined, s));
  useDocTitle(detail.data ? `Investigate: ${detail.data.title.slice(0, 60)}` : "Investigate incident");

  const back = (
    <nav className="crumbs" aria-label="Breadcrumb">
      <Link to={`/imports/${importId}/incidents${backSearch}`}>Incidents</Link>
      <span aria-hidden="true">/</span>
      <span className="muted">{imp.data?.filename ?? `import ${importId}`}</span>
    </nav>
  );

  if (detail.error) {
    const notFound = detail.error instanceof ApiError && detail.error.status === 404;
    if (notFound) forgetImport(importId);
    return (
      <>
        {back}
        <h1 className="page-title">Incident</h1>
        {notFound ? (
          <EmptyState title="Incident not found" action={<LinkButton to={`/imports/${importId}/incidents`}>Back to incidents</LinkButton>}>
            This incident is not part of that import. The import may have been deleted.
          </EmptyState>
        ) : (
          <ErrorState title="Could not load this incident" error={detail.error} onRetry={detail.reload} />
        )}
      </>
    );
  }
  if (!detail.data) {
    return (
      <>
        {back}
        <h1 className="page-title">Incident</h1>
        <LoadingBlock label="Loading incident" rows={6} />
      </>
    );
  }
  const d = detail.data;

  return (
    <>
      {back}
      <Header d={d} importId={importId} onStatus={(s) => detail.setData((p) => (p ? { ...p, status: s } : p))} />
      <div className="investigation">
        <div className="inv-main">
          <IncidentTimeline d={d} imp={imp.data} impLoading={imp.loading} />
          <Representative d={d} />
          <Records importId={importId} fingerprint={fingerprint} total={d.count} />
        </div>
        <aside className="inv-side" aria-label="Notes, related incidents and export">
          <Notes d={d} onChange={(notes) => detail.setData((p) => (p ? { ...p, notes } : p))} />
          <Related d={d} importId={importId} />
          <Panel title="Export report">
            <p className="muted small">Exports every incident in this import, with status, counts and timing.</p>
            <ExportButtons importId={importId} />
          </Panel>
        </aside>
      </div>
    </>
  );
}

function Header({ d, importId, onStatus }: { d: IncidentDetail; importId: number; onStatus: (s: IncidentDetail["status"]) => void }) {
  const { fmt } = useTime();
  const a = d.activity;
  return (
    <header className="inv-head">
      <div className="inv-title-row">
        <h1 className="inv-title mono" title={d.title}>
          {d.title}
        </h1>
        <div className="inv-status">
          <StatusSelect fingerprint={d.fingerprint} value={d.status} label={d.title} onChanged={onStatus} size="md" />
        </div>
      </div>
      <div className="inv-badges">
        <LevelBadge level={d.max_level} />
        <ActivityBadge state={a.state} />
        <span className="chip mono" title="Service">
          {d.service}
        </span>
        {d.exc_type ? (
          <span className="chip mono" title="Exception type">
            {d.exc_type}
          </span>
        ) : null}
      </div>
      <dl className="stat-strip">
        <div className="stat">
          <dt>Occurrences</dt>
          <dd className="num">{fmtInt(d.count)}</dd>
          {d.untimed_count > 0 ? <dd className="stat-hint">{fmtInt(d.untimed_count)} without timestamp</dd> : null}
        </div>
        <div className="stat">
          <dt>First seen</dt>
          <dd className="num small-val">{fmt.dateTime(d.first_seen)}</dd>
        </div>
        <div className="stat">
          <dt>Last seen</dt>
          <dd className="num small-val">{fmt.dateTime(d.last_seen)}</dd>
        </div>
        <div className="stat">
          <dt>Frequency</dt>
          <dd className="num">{fmtRate(a.rate_per_minute)}</dd>
          <dd className="stat-hint">{recurrenceLabel(a.recurrence, a.period_seconds)}</dd>
        </div>
        <div className="stat">
          <dt>Active windows</dt>
          <dd className="num">{fmtInt(a.active_windows)}</dd>
          <dd className="stat-hint">
            of {d.window_seconds}s each{a.peak_window_count > 0 ? `; peak ${fmtInt(a.peak_window_count)}` : ""}
          </dd>
        </div>
      </dl>
      <p className="muted small inv-id">
        Fingerprint <span className="mono">{d.fingerprint}</span> &middot; import #{importId}
        {d.frame ? (
          <>
            {" "}
            &middot; top frame <span className="mono">{d.frame}</span>
          </>
        ) : null}
      </p>
    </header>
  );
}

function IncidentTimeline({ d, imp, impLoading }: { d: IncidentDetail; imp: ImportDetail | null; impLoading: boolean }) {
  const { fmt } = useTime();
  const [ctx, setCtx] = useState(false);
  const step = (d.window_seconds || 60) * 1000;
  const report = imp?.anomaly && !imp.anomaly.error ? imp.anomaly : null;

  const model = useMemo(() => {
    const counts = new Map<number, number>();
    for (const w of d.windows) {
      const ms = toMs(w.start);
      if (ms !== null) counts.set(ms, (counts.get(ms) ?? 0) + w.count);
    }
    const timeline = imp?.timeline ?? [];
    let lo = Math.min(...[...counts.keys(), ...(timeline.length ? [timeline[0]!.start_ms] : [])]);
    let hi = Math.max(...[...counts.keys(), ...(timeline.length ? [timeline[timeline.length - 1]!.start_ms] : [])]);
    if (!Number.isFinite(lo) || !Number.isFinite(hi)) return null;
    const s = imp ? stepOf(timeline, imp.window_seconds) : step;
    lo = Math.floor(lo / s) * s;
    hi = Math.floor(hi / s) * s;
    const buckets: ChartBucket[] = [];
    const context: number[] = [];
    const ctxByStart = new Map(timeline.map((b) => [b.start_ms, b.error + b.critical]));
    for (let t = lo; t <= hi; t += s) {
      buckets.push({ startMs: t, values: [counts.get(t) ?? 0] });
      context.push(ctxByStart.get(t) ?? 0);
    }
    return { buckets, context, step: s };
  }, [d.windows, imp, step]);

  const bands = useMemo(() => episodeBands(report), [report]);
  const peak = d.windows.reduce((p, w) => (w.count > p.count ? w : p), { start: "", count: 0 });
  const summary = `Occurrences of this incident per ${d.window_seconds}-second window. ${fmtInt(d.count)} in total across ${d.windows.length} active windows${peak.count > 0 ? `, peak ${peak.count} at ${fmt.dateTime(peak.start)}` : ""}.`;

  return (
    <Panel
      title="When it happened"
      actions={
        imp ? (
          <label className="check">
            <input type="checkbox" checked={ctx} onChange={(e) => setCtx(e.target.checked)} />
            Show import-wide errors for context
          </label>
        ) : null
      }
    >
      {!model ? (
        impLoading ? (
          <Skeleton h={200} />
        ) : (
          <p className="muted">No timestamped occurrences, so there is no timeline to draw.</p>
        )
      ) : (
        <>
          <TimelineChart
            buckets={model.buckets}
            stepMs={model.step}
            series={[{ key: "count", label: "This incident", tone: "c-error" }]}
            context={ctx ? model.context : undefined}
            bands={bands}
            ariaLabel={summary}
            unit="occurrences per window"
            tooltip={(i): TooltipContent => {
              const b = model.buckets[i]!;
              const rows: TooltipContent["rows"] = [{ label: "This incident", value: fmtNum(b.values[0] ?? 0), tone: "c-error" }];
              if (ctx) rows.push({ label: "All errors in import", value: fmtNum(model.context[i] ?? 0), tone: "c-context" });
              const w = anomalyAt(report, b.startMs, i);
              return { title: `${fmt.dateTime(b.startMs)} (${d.window_seconds}s window)`, rows, note: w?.is_anomalous ? `Unusual window for the import: ${classificationLabel(w.classification)}${w.severity ? `, severity ${w.severity}` : ""}.` : undefined };
            }}
          />
          <p className="muted small chart-foot">
            Occurrences per {d.window_seconds}-second window, times in {fmt.label}.
            {bands.length > 0 ? " Hatched bands are unusual periods found for the whole import; they are statistical findings, not causes." : ""}
          </p>
        </>
      )}
    </Panel>
  );
}

function Representative({ d }: { d: IncidentDetail }) {
  return (
    <Panel
      title="Representative record"
      actions={
        <CopyButton text={d.sample_message} label="Copy message of the representative record">
          Copy message
        </CopyButton>
      }
    >
      <p className="muted small">First recorded occurrence of this group.</p>
      <pre className="message-block mono" tabIndex={0} aria-label="Representative message">
        {d.sample_message || "(empty message)"}
      </pre>
      {d.sample_stack_trace ? <StackTrace trace={d.sample_stack_trace} /> : <p className="muted small">No stack trace was captured for this record.</p>}
    </Panel>
  );
}

function Records({ importId, fingerprint, total }: { importId: number; fingerprint: string; total: number }) {
  const [offset, setOffset] = useState(0);
  const state = useAsync<Page<EventOut>>(`events:${importId}:${fingerprint}:${offset}`, (s) => api.get<Page<EventOut>>(`/imports/${importId}/incidents/${fingerprint}/events`, { limit: EVENT_PAGE, offset }, s));
  return (
    <Panel title={`Log records (${fmtInt(total)})`}>
      {state.error ? (
        <ErrorState title="Could not load records" error={state.error} onRetry={state.reload} />
      ) : !state.data ? (
        <LoadingBlock label="Loading records" rows={5} />
      ) : state.data.items.length === 0 ? (
        <p className="muted">No records.</p>
      ) : (
        <>
          <ol className="events" start={offset + 1}>
            {state.data.items.map((e) => (
              <EventRow key={e.event_id} e={e} />
            ))}
          </ol>
          <Pagination total={state.data.total} limit={EVENT_PAGE} offset={offset} noun="Records" onOffset={setOffset} />
        </>
      )}
    </Panel>
  );
}

function eventAsText(e: EventOut, tsLabel: string): string {
  const head = [tsLabel, e.level, e.logger ?? "", `line ${e.line_no}`].filter(Boolean).join(" ");
  return [head, e.message, e.stack_trace ?? ""].filter((s) => s !== "").join("\n");
}

function EventRow({ e }: { e: EventOut }) {
  const { fmt } = useTime();
  const [open, setOpen] = useState(false);
  const id = useId();
  const fields = Object.entries(e.fields);
  const metrics = Object.entries(e.metrics);
  const expandable = Boolean(e.stack_trace) || fields.length > 0 || metrics.length > 0;
  return (
    <li className="event">
      <div className="event-head">
        <span className="event-time num">{e.timestamp ? fmt.dateTimeMs(e.timestamp) : "no timestamp"}</span>
        <LevelBadge level={e.level} />
        {e.logger ? (
          <span className="event-logger mono muted" title={e.logger}>
            {e.logger}
          </span>
        ) : null}
        <span className="event-line mono muted" title="Line number in the source file">
          L{fmtInt(e.line_no)}
        </span>
        <span className="event-actions">
          <CopyButton text={eventAsText(e, e.timestamp ? fmt.dateTimeMs(e.timestamp) : "")} label={`Copy record from line ${e.line_no}`} />
          {expandable ? (
            <Button size="sm" variant="ghost" aria-expanded={open} aria-controls={id} onClick={() => setOpen((o) => !o)}>
              {open ? "Hide details" : "Details"}
            </Button>
          ) : null}
        </span>
      </div>
      <p className="event-msg mono">{e.message || "(empty message)"}</p>
      {open ? (
        <div id={id} className="event-detail">
          {e.stack_trace ? <StackTrace trace={e.stack_trace} /> : null}
          {fields.length > 0 ? (
            <dl className="kv-grid" aria-label="Fields">
              {fields.map(([k, v]) => (
                <div key={k}>
                  <dt className="mono">{k}</dt>
                  <dd className="mono">{v}</dd>
                </div>
              ))}
            </dl>
          ) : null}
          {metrics.length > 0 ? (
            <dl className="kv-grid" aria-label="Metrics">
              {metrics.map(([k, v]) => (
                <div key={k}>
                  <dt className="mono">{k}</dt>
                  <dd className="mono num">{fmtNum(v)}</dd>
                </div>
              ))}
            </dl>
          ) : null}
        </div>
      ) : null}
    </li>
  );
}

function Notes({ d, onChange }: { d: IncidentDetail; onChange: (n: NoteOut[]) => void }) {
  const { fmt } = useTime();
  const { push } = useToast();
  const [body, setBody] = useState("");
  const [busy, setBusy] = useState(false);
  const [target, setTarget] = useState<NoteOut | null>(null);
  const areaId = useId();
  const notes = [...d.notes].sort((a, b) => b.id - a.id);

  const add = async (e: FormEvent) => {
    e.preventDefault();
    const text = body.trim();
    if (!text) return;
    setBusy(true);
    try {
      const n = await api.post<NoteOut>(`/incidents/${d.fingerprint}/notes`, { body: text });
      onChange([...d.notes, n]);
      setBody("");
      push("Note added.");
    } catch (err) {
      push(err instanceof Error ? `Could not add note: ${err.message}` : "Could not add note.", "error");
    } finally {
      setBusy(false);
    }
  };

  const remove = async () => {
    if (!target) return;
    setBusy(true);
    try {
      await api.del(`/incidents/${d.fingerprint}/notes/${target.id}`);
      onChange(d.notes.filter((n) => n.id !== target.id));
      setTarget(null);
      push("Note deleted.");
    } catch (err) {
      push(err instanceof Error ? `Could not delete note: ${err.message}` : "Could not delete note.", "error");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Panel title={`Investigation notes (${notes.length})`}>
      <form onSubmit={add} className="stack-sm">
        <label className="field-label" htmlFor={areaId}>
          Add a note
        </label>
        <textarea id={areaId} className="input textarea" rows={3} maxLength={5000} value={body} onChange={(e) => setBody(e.target.value)} placeholder="What did you check? What did you find?" />
        <div className="form-actions">
          <Button type="submit" variant="primary" size="sm" busy={busy} disabled={body.trim() === ""}>
            Add note
          </Button>
          <span className="muted small">Saved with this error fingerprint.</span>
        </div>
      </form>
      {notes.length === 0 ? (
        <p className="muted small notes-empty">No notes yet.</p>
      ) : (
        <ul className="notes" aria-label="Notes">
          {notes.map((n) => (
            <li key={n.id} className="note">
              <p className="note-body">{n.body}</p>
              <div className="note-foot">
                <span className="muted small num">{fmt.dateTime(n.created_at)}</span>
                <Button size="sm" variant="ghost" onClick={() => setTarget(n)} aria-label={`Delete note from ${fmt.dateTime(n.created_at)}`}>
                  Delete
                </Button>
              </div>
            </li>
          ))}
        </ul>
      )}
      <ConfirmDialog open={target !== null} title="Delete this note?" body={<p className="note-preview">{target?.body}</p>} confirmLabel="Delete note" onConfirm={() => void remove()} onCancel={() => setTarget(null)} busy={busy} />
    </Panel>
  );
}

function Related({ d, importId }: { d: IncidentDetail; importId: number }) {
  const { fmt } = useTime();
  const base = toMs(d.first_seen);
  const rel = (g: IncidentItem) => {
    const t = toMs(g.first_seen);
    if (base === null || t === null) return "";
    const diff = t - base;
    if (Math.abs(diff) < 1000) return "began at the same time";
    return `began ${fmtDuration(diff)} ${diff > 0 ? "after" : "before"} this`;
  };
  return (
    <Panel title="Started around the same time">
      {d.related.length === 0 ? (
        <p className="muted small">No other error groups began near {fmt.dateTime(d.first_seen)}.</p>
      ) : (
        <>
          <p className="muted small">Other errors that first appeared close to this one. Worth checking together; proximity is not proof of a shared cause.</p>
          <ul className="related">
            {d.related.map((g) => (
              <li key={g.fingerprint}>
                <Link to={`/imports/${importId}/incidents/${g.fingerprint}`} className="mono related-title" title={g.title}>
                  {g.title}
                </Link>
                <div className="top-meta">
                  <LevelBadge level={g.max_level} />
                  <span className="num strong">{fmtInt(g.count)}&times;</span>
                  <span className="muted small">{rel(g)}</span>
                </div>
              </li>
            ))}
          </ul>
        </>
      )}
    </Panel>
  );
}
