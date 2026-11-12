import { useEffect, useId, useRef } from "react";
import type { ReactNode } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { ApiError, api, downloadComparison } from "../api/client";
import type { CompareBasis, CompareFilter, CompareItem, CompareParameters, CompareRates, CompareResult, CompareSide, ImportSummary, Page } from "../api/types";
import { DownloadButtons } from "../components/IncidentControls";
import { Button, Callout, EmptyState, ErrorState, Field, LinkButton, LoadingBlock, Pagination, Panel, SrOnly, StatusBadge } from "../components/ui";
import { fmtDuration, fmtInt } from "../lib/format";
import { useAsync } from "../lib/useAsync";
import { useTime } from "../state/app";
import { useDocTitle } from "./common";

const PAGE_SIZE = 50;

type Kind = "new" | "disappeared" | "increased" | "decreased" | "no_clear_change" | "insufficient_data";

const FINDING: Record<Kind, { label: string; hint: string }> = {
  new: { label: "New", hint: "only in target capture" },
  disappeared: { label: "Disappeared", hint: "absent from target capture" },
  increased: { label: "Increased", hint: "in both captures" },
  decreased: { label: "Decreased", hint: "in both captures" },
  no_clear_change: { label: "No clear change", hint: "in both captures" },
  insufficient_data: { label: "Insufficient data", hint: "in both captures" },
};

const FILTERS: { id: CompareFilter | ""; label: string }[] = [
  { id: "", label: "All" },
  { id: "new", label: "New" },
  { id: "increased", label: "Increased" },
  { id: "decreased", label: "Decreased" },
  { id: "no_clear_change", label: "No clear change" },
  { id: "recurring", label: "Recurring" },
  { id: "disappeared", label: "Disappeared" },
  { id: "insufficient_data", label: "Insufficient data" },
  { id: "resolved_observed", label: "Marked resolved & observed" },
];

const EVIDENCE: Record<CompareItem["evidence"], string> = {
  significant: "Significant",
  weak: "Weak evidence",
  low_sample: "Few occurrences",
};

const BASIS_LABEL: Record<CompareBasis, string> = { events: "Per 1,000 events", time: "Per minute" };
const BASIS_UNIT: Record<CompareBasis, string> = { events: "per 1k events", time: "per min" };

const sig = new Intl.NumberFormat("en-US", { maximumSignificantDigits: 3 });
const fmtSig = (n: number): string => sig.format(n);

const isFilter = (v: string | null): v is CompareFilter => FILTERS.some((f) => f.id !== "" && f.id === v);
const toId = (v: string | null): number | null => {
  const n = Number(v);
  return v && Number.isInteger(n) && n > 0 ? n : null;
};

/**
 * Fill a missing side of the pair. Neither given: the two most recent imports (older = baseline).
 * Only the target: the next older import as baseline. Only the baseline: the next newer import as target.
 */
function completePair(imports: ImportSummary[], baseline: number | null, target: number | null): [number, number] | null {
  if (baseline !== null && target !== null) return [baseline, target];
  // newest first: created_at, then id
  const sorted = [...imports].sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at) || b.id - a.id);
  if (target !== null) {
    const i = sorted.findIndex((x) => x.id === target);
    const b = (i >= 0 ? sorted[i + 1] : undefined) ?? sorted.find((x) => x.id !== target);
    return b ? [b.id, target] : null;
  }
  if (baseline !== null) {
    const i = sorted.findIndex((x) => x.id === baseline);
    const t = (i > 0 ? sorted[i - 1] : undefined) ?? sorted.find((x) => x.id !== baseline);
    return t ? [baseline, t.id] : null;
  }
  const [newest, older] = sorted;
  return newest && older ? [older.id, newest.id] : null;
}

function KindIcon({ kind }: { kind: Kind }) {
  const s = { stroke: "currentColor", strokeWidth: 1.5, strokeLinecap: "round" as const, strokeLinejoin: "round" as const, fill: "none" };
  let body: ReactNode;
  switch (kind) {
    case "new":
      body = <path d="M6 2v8M2 6h8" {...s} />;
      break;
    case "disappeared":
      body = (
        <>
          <circle cx="6" cy="6" r="4.4" {...s} strokeDasharray="2 1.6" />
          <path d="M3.8 6h4.4" {...s} />
        </>
      );
      break;
    case "increased":
      body = <path d="M6 10.5V1.8M2.6 5.2 6 1.8l3.4 3.4" {...s} />;
      break;
    case "decreased":
      body = <path d="M6 1.5v8.7M2.6 6.8 6 10.2l3.4-3.4" {...s} />;
      break;
    case "no_clear_change":
      body = <path d="M2 4.3c1.3-1 2.7-1 4 0s2.7 1 4 0M2 8c1.3-1 2.7-1 4 0s2.7 1 4 0" {...s} />;
      break;
    case "insufficient_data":
      body = (
        <>
          <path d="M4.2 4.3a1.9 1.9 0 1 1 2.6 1.8c-.5.2-.8.6-.8 1.1v.5" {...s} />
          <path d="M6 9.8v.1" {...s} />
        </>
      );
      break;
  }
  return (
    <svg className="glyph" viewBox="0 0 12 12" width="12" height="12" aria-hidden="true" focusable="false">
      {body}
    </svg>
  );
}

export function CompareView() {
  useDocTitle("Compare imports");
  const [params, setParams] = useSearchParams();
  const baselineParam = toId(params.get("baseline"));
  const targetParam = toId(params.get("target"));
  const basis: CompareBasis = params.get("basis") === "time" ? "time" : "events";
  const rawCategory = params.get("category");
  const category = isFilter(rawCategory) ? rawCategory : "";
  const page = Math.max(1, Number(params.get("page")) || 1);
  const offset = (page - 1) * PAGE_SIZE;

  const update = (patch: Record<string, string | null>, opts: { resetPage?: boolean; replace?: boolean } = {}) => {
    const next = new URLSearchParams(params);
    for (const [k, v] of Object.entries(patch)) {
      if (v === null || v === "") next.delete(k);
      else next.set(k, v);
    }
    if (opts.resetPage !== false) next.delete("page");
    setParams(next, { replace: opts.replace ?? false });
  };

  const imports = useAsync<Page<ImportSummary>>("imports:200", (s) => api.get<Page<ImportSummary>>("/imports", { limit: 200 }, s));
  const list = imports.data?.items ?? null;

  // Fill missing ids into the URL so the comparison is shareable (replace: no extra history entry).
  useEffect(() => {
    if (!list) return;
    const pair = completePair(list, baselineParam, targetParam);
    if (!pair || (pair[0] === baselineParam && pair[1] === targetParam)) return;
    const next = new URLSearchParams({ baseline: String(pair[0]), target: String(pair[1]) });
    for (const [k, v] of params) if (k !== "baseline" && k !== "target" && k !== "page") next.set(k, v);
    setParams(next, { replace: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [list, baselineParam, targetParam]);

  const ready = baselineParam !== null && targetParam !== null;
  const same = ready && baselineParam === targetParam;
  const query = { basis, category: category || undefined, limit: PAGE_SIZE, offset };
  const key = ready && !same ? `compare:${baselineParam}:${targetParam}:${JSON.stringify(query)}` : null;
  const cmp = useAsync<CompareResult>(key, (s) => api.get<CompareResult>(`/imports/${baselineParam}/compare/${targetParam}`, query, s));

  // Keep the last result of the same pair and basis on screen while a filter or page loads.
  const last = useRef<CompareResult | null>(null);
  if (cmp.data) last.current = cmp.data;
  const prev = last.current;
  const view = cmp.data ?? (key && prev && prev.baseline.id === baselineParam && prev.target.id === targetParam && prev.basis === basis ? prev : null);

  const baselineId = useId();
  const targetId = useId();
  const basisId = useId();
  const basisHintId = useId();

  const head = (
    <div className="page-head">
      <div>
        <h1 className="page-title">Compare imports</h1>
        <p className="page-sub">Which error groups are new, more or less frequent, or absent after a change such as a deployment.</p>
      </div>
      {ready && !same ? (
        <div className="page-tools">
          <DownloadButtons groupLabel="Export this comparison" noun="comparison" download={(f) => downloadComparison(baselineParam, targetParam, f, basis)} />
        </div>
      ) : null}
    </div>
  );

  if (imports.error) {
    return (
      <>
        {head}
        <ErrorState title="Could not load imports" error={imports.error} onRetry={imports.reload} />
      </>
    );
  }
  if (!list) {
    return (
      <>
        {head}
        <LoadingBlock label="Loading imports" rows={5} />
      </>
    );
  }
  if (list.length < 2) {
    return (
      <>
        {head}
        <EmptyState title="Two imports are needed to compare" action={<LinkButton to="/import" variant="primary">Import a log file</LinkButton>}>
          Import a capture from before a change (the baseline) and one from after it (the target). {list.length === 1 ? "There is only one import so far." : "Nothing is imported yet."}
        </EmptyState>
      </>
    );
  }

  const timeUnavailable = view !== null && !view.time_comparable;
  // The list holds the newest 200 imports; keep older imports chosen via the URL selectable.
  const choices = view ? [...list, ...[view.baseline, view.target].filter((s) => !list.some((i) => i.id === s.id))] : list;
  const swap = () => {
    if (ready) update({ baseline: String(targetParam), target: String(baselineParam) });
  };

  return (
    <>
      {head}

      <div className="filters cmp-controls" role="group" aria-label="Imports to compare">
        <Field label="Baseline (before)" htmlFor={baselineId} className="cmp-pick">
          <ImportSelect id={baselineId} imports={choices} value={baselineParam} onChange={(v) => update({ baseline: String(v) })} />
        </Field>
        <div className="field field-end cmp-swap">
          <Button onClick={swap} disabled={!ready} aria-label="Swap baseline and target" title="Swap baseline and target">
            <svg className="glyph" viewBox="0 0 12 12" width="12" height="12" aria-hidden="true" focusable="false">
              <path d="M1.5 4h8.5M7.6 1.6 10 4 7.6 6.4M10.5 8H2M4.4 5.6 2 8l2.4 2.4" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
            Swap
          </Button>
        </div>
        <Field label="Target (after)" htmlFor={targetId} className="cmp-pick">
          <ImportSelect id={targetId} imports={choices} value={targetParam} onChange={(v) => update({ target: String(v) })} />
        </Field>
        <div className="field cmp-basis">
          <span className="field-label" id={basisId}>
            Rates
          </span>
          <div className="segmented" role="group" aria-labelledby={basisId}>
            <button type="button" aria-pressed={basis === "events"} onClick={() => update({ basis: null })}>
              {BASIS_LABEL.events}
            </button>
            <button type="button" aria-pressed={basis === "time"} disabled={timeUnavailable && basis !== "time"} aria-describedby={timeUnavailable ? basisHintId : undefined} onClick={() => update({ basis: "time" })}>
              {BASIS_LABEL.time}
            </button>
          </div>
          {timeUnavailable ? (
            <span className="field-hint" id={basisHintId}>
              Per minute needs at least {view.parameters.min_coverage_seconds}s of valid timestamps in both imports.
            </span>
          ) : null}
        </div>
      </div>

      {same ? (
        <Callout kind="info" title="Choose two different imports">
          Baseline and target are the same import. Pick the capture from before the change as the baseline and the one after it as the target.
        </Callout>
      ) : !ready ? (
        <LoadingBlock label="Choosing imports" rows={3} />
      ) : cmp.error ? (
        cmp.error instanceof ApiError && cmp.error.status === 422 && basis === "time" ? (
          <ErrorState title="Per-minute comparison is not available" error={cmp.error} action={<Button onClick={() => update({ basis: null })}>Compare per 1,000 events</Button>} />
        ) : (
          <ErrorState title="Could not compare these imports" error={cmp.error} onRetry={cmp.reload} />
        )
      ) : !view ? (
        <LoadingBlock label="Comparing imports" rows={7} />
      ) : (
        <Results view={view} basis={basis} category={category} offset={offset} loading={cmp.loading} onCategory={(c) => update({ category: c || null })} onOffset={(o) => update({ page: o === 0 ? null : String(o / PAGE_SIZE + 1) }, { resetPage: false })} />
      )}
    </>
  );
}

function ImportSelect({ id, imports, value, onChange }: { id: string; imports: ImportSummary[]; value: number | null; onChange: (id: number) => void }) {
  const { fmt } = useTime();
  const known = imports.some((i) => i.id === value);
  return (
    <select id={id} className="select" value={known ? String(value) : ""} onChange={(e) => onChange(Number(e.target.value))}>
      {!known ? (
        <option value="" disabled>
          {value === null ? "Choose an import" : `Import #${value}`}
        </option>
      ) : null}
      {imports.map((i) => (
        <option key={i.id} value={i.id}>
          {i.filename} - {fmt.date(i.created_at)} (#{i.id})
        </option>
      ))}
    </select>
  );
}

function Results({ view, basis, category, offset, loading, onCategory, onOffset }: { view: CompareResult; basis: CompareBasis; category: CompareFilter | ""; offset: number; loading: boolean; onCategory: (c: CompareFilter | "") => void; onOffset: (o: number) => void }) {
  const t = view.totals;
  const count = (id: CompareFilter | "") => (id === "" ? t.new + t.disappeared + t.recurring : t[id]);
  const current = FILTERS.find((f) => f.id === category)?.label ?? "All";
  return (
    <>
      <div className="cmp-sides">
        <SideSummary title="Baseline capture (before)" side={view.baseline} />
        <span className="cmp-arrow" aria-hidden="true">
          &rarr;
        </span>
        <SideSummary title="Target capture (after)" side={view.target} />
      </div>

      {view.warnings.length > 0 ? (
        <Callout kind="warning" title="Check these before drawing conclusions">
          <ul className="cmp-warnings">
            {view.warnings.map((w) => (
              <li key={w.code} data-code={w.code}>
                {w.message}
              </li>
            ))}
          </ul>
        </Callout>
      ) : null}

      <HowToRead p={view.parameters} />

      <div className="cmp-chips" role="group" aria-label="Show findings">
        {FILTERS.map((f) => (
          <button key={f.id || "all"} type="button" className="btn btn-secondary btn-sm cmp-chip" aria-pressed={category === f.id} onClick={() => onCategory(f.id)}>
            {f.label}{" "}
            <span className="cmp-chip-n">{fmtInt(count(f.id))}</span>
          </button>
        ))}
      </div>

      <div className="results-meta" aria-live="polite">
        {fmtInt(view.total)} error group{view.total === 1 ? "" : "s"}
        {category ? ` in "${current}"` : ""} &middot; rates {BASIS_LABEL[basis].toLowerCase()}
      </div>

      {view.items.length === 0 ? (
        <EmptyState title={category ? `No error groups in "${current}"` : "No error groups in either import"} action={category ? <Button onClick={() => onCategory("")}>Show all</Button> : undefined}>
          {category ? "Pick another finding above to see the other groups." : "Neither capture contains errors or warnings that were grouped."}
        </EmptyState>
      ) : (
        <div className={`table-wrap${loading ? " is-loading" : ""}`}>
          <table className="table table-stack cmp-table" data-testid="compare-table">
            <caption className="sr-only">
              Error groups in baseline {view.baseline.filename} (before) compared with target {view.target.filename} (after), {current.toLowerCase()}, rates {BASIS_LABEL[basis].toLowerCase()}
            </caption>
            <thead>
              <tr>
                <th scope="col">Finding</th>
                <th scope="col">Error</th>
                <th scope="col">Service</th>
                <th scope="col" className="num">
                  Before <span className="th-unit">count, {BASIS_UNIT[basis]}</span>
                </th>
                <th scope="col" className="num">
                  After <span className="th-unit">count, {BASIS_UNIT[basis]}</span>
                </th>
                <th scope="col" className="cmp-col-change">
                  Change
                </th>
                <th scope="col">Evidence</th>
                <th scope="col" className="cmp-col-status">
                  Status
                </th>
              </tr>
            </thead>
            <tbody>
              {view.items.map((it) => (
                <Row key={it.fingerprint} it={it} basis={basis} p={view.parameters} />
              ))}
            </tbody>
          </table>
        </div>
      )}
      <Pagination total={view.total} limit={PAGE_SIZE} offset={offset} noun="Compared error groups" onOffset={onOffset} />
    </>
  );
}

function SideSummary({ title, side }: { title: string; side: CompareSide }) {
  const { fmt } = useTime();
  return (
    <Panel className="cmp-side" title={title}>
      <dl className="cmp-facts">
        <dt>File</dt>
        <dd className="mono">
          {side.filename} <span className="muted">(#{side.id})</span>
        </dd>
        <dt>Events</dt>
        <dd>{fmtInt(side.event_count)}</dd>
        <dt>Time range ({fmt.label})</dt>
        <dd>
          {side.first_event ? (
            <>
              {fmt.dateTimeBare(side.first_event)}
              <span className="muted"> to </span>
              {fmt.dateTimeBare(side.last_event)}
            </>
          ) : (
            "no timestamps"
          )}
        </dd>
        <dt>Duration</dt>
        <dd>{side.duration_seconds !== null ? fmtDuration(side.duration_seconds * 1000) : "unknown"}</dd>
        <dt>Untimed events</dt>
        <dd>{fmtInt(side.untimed_events)}</dd>
      </dl>
    </Panel>
  );
}

function HowToRead({ p }: { p: CompareParameters }) {
  const fold = fmtSig(p.min_fold_change);
  return (
    <details className="cmp-help">
      <summary>How to read this</summary>
      <ul className="cmp-help-body">
        <li>
          <strong>Baseline</strong> is the capture from before the change, <strong>target</strong> the one after it. Both are snapshots: a different capture length or traffic mix changes what you see.
        </li>
        <li>
          <strong>Per 1,000 events</strong> divides each group&rsquo;s count by all events in its import, so an error that only grows with traffic is not flagged. <strong>Per minute</strong> divides by capture duration; it needs at least {fmtInt(p.min_coverage_seconds)}s of valid timestamps in both imports. When the two bases disagree, the row says so: traffic volume or capture length changed.
        </li>
        <li>
          <strong>Increased</strong> or <strong>Decreased</strong> requires both a Benjamini-Hochberg adjusted p-value (q) below {p.alpha} across all compared groups and a rate change of at least &times;{fold} (or down to &times;{fmtSig(1 / p.min_fold_change)}). Otherwise the group shows <strong>No clear change</strong>; <em>weak evidence</em> means the difference could be chance.
        </li>
        <li>
          <strong>Insufficient data</strong> and <em>few occurrences</em>: fewer than {fmtInt(p.min_occurrences)} occurrences in both captures together, too few to judge.
        </li>
        <li>
          <strong>Disappeared</strong> means absent from the target capture, not fixed: the capture may be shorter or see different traffic, or the fingerprint may have changed (for example a shifted line number).
        </li>
        <li>
          <strong>Marked resolved, observed in target</strong>: someone set the status to resolved and the group appears in the target. The status has no fix time, so check whether the target was captured after the fix before calling it a regression.
        </li>
      </ul>
    </details>
  );
}

function RateCell({ r, basis, absent }: { r: CompareRates | null; basis: CompareBasis; absent: string }) {
  if (!r) {
    return (
      <div className="cmp-cell">
        <span className="muted">0</span>
        <span className="cmp-sub">{absent}</span>
      </div>
    );
  }
  const rate = basis === "events" ? r.per_1k_events : r.per_minute;
  return (
    <div className="cmp-cell">
      <span className="strong">{fmtInt(r.count)}</span>
      <span className="cmp-sub">{rate === null ? "no timed events" : `${fmtSig(rate)} ${BASIS_UNIT[basis]}`}</span>
    </div>
  );
}

function ChangeCell({ it, basis }: { it: CompareItem; basis: CompareBasis }) {
  let main: ReactNode;
  if (it.category === "new") main = <span className="strong">new</span>;
  else if (it.category === "disappeared") main = <span className="strong">absent from target</span>;
  else if (it.rate_ratio === null) main = <span className="muted">-</span>;
  else {
    const pct = it.percent_change;
    main = (
      <>
        <span className="strong">&times;{fmtSig(it.rate_ratio)}</span>
        {pct !== null ? <span className="cmp-sub">{`${pct > 0 ? "+" : pct < 0 ? "\u2212" : ""}${fmtSig(Math.abs(pct))}%`}</span> : null}
      </>
    );
  }
  const other = basis === "events" ? "Per-minute" : "Per-1,000-events";
  const why = `${other} rates would classify this group differently: traffic volume or capture length changed between the imports.`;
  return (
    <div className="cmp-cell">
      {main}
      {it.bases_disagree ? (
        <span className="cmp-note" title={why}>
          <svg className="glyph" viewBox="0 0 12 12" width="12" height="12" aria-hidden="true" focusable="false">
            <circle cx="6" cy="6" r="5" fill="none" stroke="currentColor" strokeWidth="1.3" />
            <path d="M6 5.4v3M6 3.5v.1" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
          </svg>
          {other} rate tells a different story
          <SrOnly>: traffic volume or capture length changed between the imports.</SrOnly>
        </span>
      ) : null}
    </div>
  );
}

function Row({ it, basis, p }: { it: CompareItem; basis: CompareBasis; p: CompareParameters }) {
  const { fmt } = useTime();
  const kind: Kind = it.category === "recurring" ? (it.change ?? "no_clear_change") : it.category;
  const f = FINDING[kind];
  const q = it.q_value === null ? null : `q = ${fmtQ(it.q_value, p.alpha)}`;
  const evidenceTitle =
    it.evidence === "significant" ? `Unlikely to be chance (${q}, below ${p.alpha})` : it.evidence === "weak" ? `Could be chance${q ? ` (${q})` : ""}` : `Fewer than ${p.min_occurrences} occurrences in both captures together`;
  const resolvedTitle = `Status is resolved${it.status_updated_at ? ` (last changed ${fmt.dateTime(it.status_updated_at)})` : ""} and this group appears in the target capture. The status has no fix time, so this alone does not confirm a regression.`;
  return (
    <tr>
      <td data-label="Finding">
        <div className="cmp-cell">
          <span className={`badge cmp-kind cmp-${kind}`}>
            <KindIcon kind={kind} />
            {f.label}
          </span>
          <span className="cmp-sub">{f.hint}</span>
        </div>
      </td>
      <td data-label="Error" className="cell-title">
        <Link to={`/imports/${it.investigate_import_id}/incidents/${it.fingerprint}`} className="title-link mono" title={it.title}>
          {it.title}
        </Link>
      </td>
      <td data-label="Service" className="ellipsis-cell">
        <span className="mono small" title={it.service}>
          {it.service}
        </span>
      </td>
      <td data-label="Before" className="num">
        <RateCell r={it.baseline} basis={basis} absent="not in baseline" />
      </td>
      <td data-label="After" className="num">
        <RateCell r={it.target} basis={basis} absent="not in target" />
      </td>
      <td data-label="Change" className="cmp-col-change">
        <ChangeCell it={it} basis={basis} />
      </td>
      <td data-label="Evidence">
        <div className="cmp-cell">
          <span className={`badge cmp-ev cmp-ev-${it.evidence}`} title={evidenceTitle}>
            {EVIDENCE[it.evidence]}
          </span>
          {q ? <span className="cmp-sub mono">{q}</span> : null}
        </div>
      </td>
      <td data-label="Status" className="cmp-col-status">
        <div className="cmp-cell">
          <StatusBadge status={it.status} />
          {it.resolved_observed ? (
            <span className="badge cmp-flag" title={resolvedTitle}>
              Marked resolved, observed in target
            </span>
          ) : null}
        </div>
      </td>
    </tr>
  );
}

/** Two significant digits, with more when rounding would put the value on the other side of alpha (0.0499 -> 0.0499, not 0.050). */
function fmtQ(q: number, alpha: number): string {
  for (let digits = 2; digits < 8; digits++) {
    const s = q.toPrecision(digits);
    if (Number(s) < alpha === q < alpha) return s;
  }
  return q.toPrecision(8);
}
