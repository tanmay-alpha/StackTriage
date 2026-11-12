import { useEffect } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import type { ImportDetail, ImportSummary, IncidentItem, Page } from "../api/types";
import { UnusualPeriods } from "../components/Anomaly";
import { DiagnosticsSummaryPanel } from "../components/DiagnosticsPanels";
import { ImportPicker } from "../components/ImportPicker";
import { MetricsChart, VolumeChart } from "../components/VolumeChart";
import { ActivityBadge, ErrorState, EmptyState, LevelBadge, LinkButton, LoadingBlock, Panel, Skeleton } from "../components/ui";
import { fmtBytes, fmtInt, recurrenceLabel } from "../lib/format";
import { rememberImport } from "../lib/lastImport";
import { useAsync } from "../lib/useAsync";
import type { AsyncState } from "../lib/useAsync";
import type { ReactNode } from "react";
import { useTime } from "../state/app";
import { useDocTitle } from "./common";

function Stat({ label, value, hint }: { label: string; value: ReactNode; hint?: string }) {
  return (
    <div className="stat">
      <dt>{label}</dt>
      <dd className="num">{value}</dd>
      {hint ? <dd className="stat-hint">{hint}</dd> : null}
    </div>
  );
}

export function OverviewView() {
  useDocTitle("Overview");
  const [params, setParams] = useSearchParams();
  const imports = useAsync<Page<ImportSummary>>("imports:200", (s) => api.get<Page<ImportSummary>>("/imports", { limit: 200 }, s));
  const list = imports.data?.items ?? [];
  const wanted = Number(params.get("import"));
  const selected = list.find((i) => i.id === wanted) ?? list[0] ?? null;
  const id = selected?.id ?? null;

  useEffect(() => {
    if (id !== null) rememberImport(id);
  }, [id]);

  const detail = useAsync<ImportDetail>(id === null ? null : `import:${id}`, (s) => api.get<ImportDetail>(`/imports/${id}`, undefined, s));
  const top = useAsync<Page<IncidentItem>>(id === null ? null : `top:${id}`, (s) => api.get<Page<IncidentItem>>(`/imports/${id}/incidents`, { limit: 8, sort: "count", order: "desc" }, s));

  if (imports.loading && !imports.data) {
    return (
      <>
        <h1 className="page-title">Overview</h1>
        <LoadingBlock label="Loading overview" rows={6} />
      </>
    );
  }
  if (imports.error) {
    return (
      <>
        <h1 className="page-title">Overview</h1>
        <ErrorState title="Could not load imports" error={imports.error} onRetry={imports.reload} />
      </>
    );
  }
  if (!selected) {
    return (
      <>
        <h1 className="page-title">Overview</h1>
        <EmptyState
          title="Nothing imported yet"
          action={
            <LinkButton to="/import" variant="primary">
              Import a log file
            </LinkButton>
          }
        >
          Upload a Python, Node.js, Java Spring, JSONL or CSV log. StackTriage groups the errors, shows which repeat and when unusual activity started.
        </EmptyState>
      </>
    );
  }

  return (
    <>
      <div className="page-head">
        <div>
          <h1 className="page-title">Overview</h1>
          <p className="page-sub">
            <span className="mono strong">{selected.filename}</span>
          </p>
        </div>
        <div className="page-tools">
          {list.length > 1 ? (
            <>
              <ImportPicker imports={list} value={selected.id} onChange={(v) => setParams({ import: String(v) }, { replace: true })} />
              <LinkButton to={`/compare?target=${selected.id}`}>Compare with another import</LinkButton>
            </>
          ) : null}
          <LinkButton to={`/imports/${selected.id}/incidents`} variant="primary">
            View incidents
          </LinkButton>
        </div>
      </div>

      <SummaryStrip imp={selected} />

      {detail.error ? (
        <ErrorState title="Could not load this import" error={detail.error} onRetry={detail.reload} />
      ) : !detail.data ? (
        <Panel title="Log volume over time">
          <Skeleton h={240} />
        </Panel>
      ) : (
        <>
          <VolumeChart timeline={detail.data.timeline} windowSeconds={detail.data.window_seconds} anomaly={detail.data.anomaly} />
          <div className="grid-2">
            <UnusualPeriods report={detail.data.anomaly} />
            <TopGroups importId={selected.id} state={top} />
          </div>
          <MetricsChart timeline={detail.data.timeline} windowSeconds={detail.data.window_seconds} anomaly={detail.data.anomaly} />
          <div className="grid-2">
            <DiagnosticsSummaryPanel d={detail.data.diagnostics} notes={detail.data.notes} />
            <LevelBreakdown d={detail.data} />
          </div>
        </>
      )}
    </>
  );
}

function SummaryStrip({ imp }: { imp: ImportSummary }) {
  const { fmt } = useTime();
  return (
    <dl className="stat-strip" aria-label="Import summary">
      <Stat label="Format" value={<span className="mono">{imp.format}</span>} hint={imp.default_service ? `service: ${imp.default_service}` : undefined} />
      <Stat label="Events" value={fmtInt(imp.event_count)} hint={fmtBytes(imp.size_bytes)} />
      <Stat label="Error groups" value={fmtInt(imp.incident_count)} hint={`${imp.window_seconds}s windows`} />
      <div className="stat stat-wide">
        <dt>Time range ({fmt.label})</dt>
        <dd className="num small-val">
          {imp.first_event ? (
            <>
              {fmt.dateTime(imp.first_event).replace(` ${fmt.label}`, "")}
              <span className="muted"> to </span>
              {fmt.dateTime(imp.last_event).replace(` ${fmt.label}`, "")}
            </>
          ) : (
            "no timestamps"
          )}
        </dd>
      </div>
    </dl>
  );
}

function LevelBreakdown({ d }: { d: ImportDetail }) {
  const entries = Object.entries(d.level_counts).sort((a, b) => b[1] - a[1]);
  const services = Object.entries(d.service_counts).sort((a, b) => b[1] - a[1]);
  const max = Math.max(1, ...entries.map((e) => e[1]));
  return (
    <Panel title="Events by level and service">
      <ul className="bar-list" aria-label="Events by level">
        {entries.map(([lvl, n]) => (
          <li key={lvl}>
            <LevelBadge level={lvl} />
            <span className="bar-track" aria-hidden="true">
              <span className={`bar-fill lvl-${lvl.toLowerCase()}-fill`} style={{ width: `${(n / max) * 100}%` }} />
            </span>
            <span className="num">{fmtInt(n)}</span>
          </li>
        ))}
      </ul>
      {services.length > 0 ? (
        <>
          <h3 className="subhead">Services</h3>
          <ul className="kv-list">
            {services.slice(0, 6).map(([s, n]) => (
              <li key={s}>
                <span className="mono">{s}</span>
                <span className="num">{fmtInt(n)}</span>
              </li>
            ))}
          </ul>
        </>
      ) : null}
    </Panel>
  );
}

function TopGroups({ importId, state }: { importId: number; state: AsyncState<Page<IncidentItem>> }) {
  const { fmt } = useTime();
  return (
    <Panel title="Top error groups" actions={<Link to={`/imports/${importId}/incidents`}>All incidents</Link>}>
      {state.error ? (
        <ErrorState title="Could not load error groups" error={state.error} onRetry={state.reload} />
      ) : !state.data ? (
        <LoadingBlock label="Loading error groups" />
      ) : state.data.items.length === 0 ? (
        <p className="muted">No error groups in this import.</p>
      ) : (
        <ul className="top-groups">
          {state.data.items.map((g) => (
            <li key={g.fingerprint}>
              <Link to={`/imports/${importId}/incidents/${g.fingerprint}`} className="top-title mono" title={g.title}>
                {g.title}
              </Link>
              <div className="top-meta">
                <LevelBadge level={g.max_level} />
                <ActivityBadge state={g.activity.state} />
                <span className="num strong">{fmtInt(g.count)}&times;</span>
                <span className="muted small">{recurrenceLabel(g.activity.recurrence, g.activity.period_seconds)}</span>
                <span className="muted small">last {fmt.short(g.last_seen)}</span>
              </div>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}
