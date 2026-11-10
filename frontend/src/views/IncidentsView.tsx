import { useEffect, useId, useRef, useState } from "react";
import { Link, useLocation, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import type { ImportDetail, ImportSummary, IncidentItem, Page, Status } from "../api/types";
import { STATUSES } from "../api/types";
import { ExportButtons, StatusSelect } from "../components/IncidentControls";
import { ImportPicker } from "../components/ImportPicker";
import { ActivityBadge, EmptyState, ErrorState, Field, LevelBadge, LinkButton, LoadingBlock, Pagination, STATUS_LABEL } from "../components/ui";
import { fmtInt, fmtRate, recurrenceLabel } from "../lib/format";
import { highlight } from "../lib/highlight";
import { forgetImport, rememberImport } from "../lib/lastImport";
import { useAsync, useDebounced } from "../lib/useAsync";
import { ApiError } from "../api/client";
import { useTime } from "../state/app";
import { useDocTitle } from "./common";

const PAGE_SIZE = 25;
type SortKey = "count" | "first_seen" | "last_seen" | "level";
const SORTS: { id: SortKey; label: string }[] = [
  { id: "count", label: "Count" },
  { id: "first_seen", label: "First seen" },
  { id: "last_seen", label: "Last seen" },
  { id: "level", label: "Level" },
];
const LEVELS = [
  { id: "", label: "All levels" },
  { id: "info", label: "Info and above" },
  { id: "warning", label: "Warning and above" },
  { id: "error", label: "Error and above" },
  { id: "critical", label: "Critical only" },
];

export function IncidentsView() {
  useDocTitle("Incidents");
  const { importId: idParam = "" } = useParams();
  const importId = Number(idParam);
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();
  const loc = useLocation();
  const { fmt } = useTime();

  const q = params.get("q") ?? "";
  const level = params.get("level") ?? "";
  const service = params.get("service") ?? "";
  const status = params.get("status") ?? "";
  const activity = params.get("activity") ?? "";
  const sort = (SORTS.some((s) => s.id === params.get("sort")) ? params.get("sort") : "count") as SortKey;
  const order = params.get("order") === "asc" ? "asc" : "desc";
  const page = Math.max(1, Number(params.get("page")) || 1);
  const offset = (page - 1) * PAGE_SIZE;

  const update = (patch: Record<string, string | null>, resetPage = true) => {
    const next = new URLSearchParams(params);
    for (const [k, v] of Object.entries(patch)) {
      if (v === null || v === "") next.delete(k);
      else next.set(k, v);
    }
    if (resetPage) next.delete("page");
    setParams(next, { replace: true });
  };

  // debounced search box
  const [text, setText] = useState(q);
  const debounced = useDebounced(text, 300);
  const pushed = useRef(q);
  useEffect(() => {
    if (q !== pushed.current) {
      pushed.current = q;
      setText(q);
    }
  }, [q]);
  useEffect(() => {
    const next = debounced.trim();
    if (next !== q) {
      pushed.current = next;
      update({ q: next === "" ? null : next });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [debounced]);

  useEffect(() => {
    if (Number.isFinite(importId)) rememberImport(importId);
  }, [importId]);

  const imports = useAsync<Page<ImportSummary>>("imports:200", (s) => api.get<Page<ImportSummary>>("/imports", { limit: 200 }, s));
  const detail = useAsync<ImportDetail>(`import:${importId}`, (s) => api.get<ImportDetail>(`/imports/${importId}`, undefined, s));

  const query = { q: q || undefined, level: level || undefined, service: service || undefined, status: status || undefined, ongoing: activity === "ongoing" ? true : activity === "ended" ? false : undefined, sort, order, limit: PAGE_SIZE, offset };
  const key = `incidents:${importId}:${JSON.stringify(query)}`;
  const list = useAsync<Page<IncidentItem>>(key, (s) => api.get<Page<IncidentItem>>(`/imports/${importId}/incidents`, query, s));

  const searchId = useId();
  const levelId = useId();
  const serviceId = useId();
  const statusId = useId();
  const activityId = useId();
  const sortId = useId();

  if (detail.error instanceof ApiError && detail.error.status === 404) {
    forgetImport(importId);
    return (
      <>
        <h1 className="page-title">Incidents</h1>
        <EmptyState title="That import no longer exists" action={<LinkButton to="/import" variant="primary">Go to Import</LinkButton>}>
          It may have been deleted. Pick another import or upload the log again.
        </EmptyState>
      </>
    );
  }

  const services = Object.keys(detail.data?.service_counts ?? {}).sort();
  const filtered = Boolean(q || level || service || status || activity);
  const link = (fp: string) => `/imports/${importId}/incidents/${fp}`;

  const toggleSort = (k: SortKey) => {
    if (sort === k) update({ order: order === "desc" ? "asc" : "desc" });
    else update({ sort: k, order: k === "level" || k === "count" || k === "last_seen" ? "desc" : "asc" });
  };

  const setStatusLocal = (fp: string, s: Status) => list.setData((p) => (p ? { ...p, items: p.items.map((i) => (i.fingerprint === fp ? { ...i, status: s } : i)) } : p));

  const sortHead = (k: SortKey, label: string, className?: string) => (
    <th scope="col" className={className} aria-sort={sort === k ? (order === "asc" ? "ascending" : "descending") : "none"}>
      <button type="button" className="th-btn" onClick={() => toggleSort(k)}>
        {label}
        <span className="sort-ind" aria-hidden="true">
          {sort === k ? (order === "asc" ? "\u25B2" : "\u25BC") : ""}
        </span>
      </button>
    </th>
  );

  return (
    <>
      <div className="page-head">
        <div>
          <h1 className="page-title">Incidents</h1>
          <p className="page-sub">
            {detail.data ? (
              <>
                <span className="mono strong">{detail.data.filename}</span> <span className="muted">&middot; {fmtInt(detail.data.event_count)} events &middot; {fmtInt(detail.data.incident_count)} error groups</span>
              </>
            ) : (
              <span className="muted">Loading import...</span>
            )}
          </p>
        </div>
        <div className="page-tools">
          {imports.data && imports.data.items.length > 1 ? <ImportPicker imports={imports.data.items} value={importId} onChange={(id) => navigate(`/imports/${id}/incidents`)} /> : null}
          <ExportButtons importId={importId} />
        </div>
      </div>

      <form className="filters" role="search" aria-label="Filter incidents" onSubmit={(e) => e.preventDefault()}>
        <Field label="Search" htmlFor={searchId} className="field-grow">
          <input id={searchId} className="input" type="search" value={text} onChange={(e) => setText(e.target.value)} placeholder="Message, exception or frame" autoComplete="off" />
        </Field>
        <Field label="Minimum level" htmlFor={levelId}>
          <select id={levelId} className="select" value={level} onChange={(e) => update({ level: e.target.value })}>
            {LEVELS.map((l) => (
              <option key={l.id} value={l.id}>
                {l.label}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Service" htmlFor={serviceId}>
          <select id={serviceId} className="select" value={service} onChange={(e) => update({ service: e.target.value })}>
            <option value="">All services</option>
            {services.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Status" htmlFor={statusId}>
          <select id={statusId} className="select" value={status} onChange={(e) => update({ status: e.target.value })}>
            <option value="">Any status</option>
            {STATUSES.map((s) => (
              <option key={s} value={s}>
                {STATUS_LABEL[s]}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Activity" htmlFor={activityId}>
          <select id={activityId} className="select" value={activity} onChange={(e) => update({ activity: e.target.value })}>
            <option value="">Any activity</option>
            <option value="ongoing">Ongoing</option>
            <option value="ended">Ended</option>
          </select>
        </Field>
        <div className="field sort-field">
          <label className="field-label" htmlFor={sortId}>
            Sort by
          </label>
          <div className="sort-ctl">
            <select id={sortId} className="select" value={sort} onChange={(e) => update({ sort: e.target.value })}>
              {SORTS.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.label}
                </option>
              ))}
            </select>
            <button type="button" className="btn btn-secondary btn-md" onClick={() => update({ order: order === "desc" ? "asc" : "desc" })} title={`Sort order: ${order === "desc" ? "descending" : "ascending"}. Click to reverse.`}>
              {order === "desc" ? "Descending" : "Ascending"}
            </button>
          </div>
        </div>
        {filtered ? (
          <div className="field field-end">
            <button
              type="button"
              className="btn btn-ghost btn-md"
              onClick={() => {
                setText("");
                setParams(new URLSearchParams(), { replace: true });
              }}
            >
              Clear filters
            </button>
          </div>
        ) : null}
      </form>

      <div className="results-meta" aria-live="polite">
        {list.data ? (
          <span>
            {fmtInt(list.data.total)} incident{list.data.total === 1 ? "" : "s"}
            {filtered ? " match the filters" : ""}
          </span>
        ) : (
          <span>&nbsp;</span>
        )}
      </div>

      {list.error ? (
        <ErrorState title="Could not load incidents" error={list.error} onRetry={list.reload} />
      ) : !list.data ? (
        <LoadingBlock label="Loading incidents" rows={7} />
      ) : list.data.items.length === 0 ? (
        <EmptyState title={filtered ? "No incidents match these filters" : "No error groups in this import"} action={filtered ? <button type="button" className="btn btn-secondary btn-md" onClick={() => { setText(""); setParams(new URLSearchParams(), { replace: true }); }}>Clear filters</button> : undefined}>
          {filtered ? "Try a broader search or lower the minimum level." : "This log has no errors or warnings that were grouped into incidents."}
        </EmptyState>
      ) : (
        <div className={`table-wrap${list.loading ? " is-loading" : ""}`}>
          <table className="table table-stack incidents" data-testid="incidents-table">
            <caption className="sr-only">Incidents, sorted by {sort.replace("_", " ")} {order === "asc" ? "ascending" : "descending"}</caption>
            <thead>
              <tr>
                <th scope="col">Status</th>
                {sortHead("level", "Level")}
                <th scope="col">Error</th>
                <th scope="col">Service</th>
                {sortHead("count", "Count", "num")}
                {sortHead("first_seen", `First seen (${fmt.label})`)}
                {sortHead("last_seen", `Last seen (${fmt.label})`)}
                <th scope="col">Frequency</th>
              </tr>
            </thead>
            <tbody>
              {list.data.items.map((g) => (
                <tr key={g.fingerprint} className={`row-${g.status}`}>
                  <td data-label="Status">
                    <StatusSelect fingerprint={g.fingerprint} value={g.status} label={g.title} onChanged={(s) => setStatusLocal(g.fingerprint, s)} />
                  </td>
                  <td data-label="Level">
                    <LevelBadge level={g.max_level} />
                  </td>
                  <td data-label="Error" className="cell-title">
                    <Link to={link(g.fingerprint)} state={{ search: loc.search }} className="title-link mono" title={g.title}>
                      {highlight(g.title, q)}
                    </Link>
                  </td>
                  <td data-label="Service" className="ellipsis-cell">
                    <span className="mono small" title={g.service}>
                      {g.service}
                    </span>
                  </td>
                  <td data-label="Count" className="num strong">
                    {fmtInt(g.count)}
                  </td>
                  <td data-label={`First seen (${fmt.label})`} className="nowrap small">
                    {fmt.dateTimeBare(g.first_seen)}
                  </td>
                  <td data-label={`Last seen (${fmt.label})`} className="nowrap small">
                    {fmt.dateTimeBare(g.last_seen)}
                  </td>
                  <td data-label="Frequency" className="freq">
                    <span className="num">{fmtRate(g.activity.rate_per_minute)}</span>
                    <span className="muted small"> {recurrenceLabel(g.activity.recurrence, g.activity.period_seconds)}</span>
                    <div>
                      <ActivityBadge state={g.activity.state} />
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {list.data ? <Pagination total={list.data.total} limit={PAGE_SIZE} offset={offset} noun="Incidents" onOffset={(o) => update({ page: o === 0 ? null : String(o / PAGE_SIZE + 1) }, false)} /> : null}
    </>
  );
}
