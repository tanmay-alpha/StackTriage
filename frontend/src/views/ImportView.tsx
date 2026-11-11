import { useEffect, useId, useRef, useState } from "react";
import type { DragEvent, FormEvent } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, uploadLog, ApiError } from "../api/client";
import type { ImportDetail, ImportSummary, Page } from "../api/types";
import { LOG_FORMATS } from "../api/types";
import { DiagnosticsTable, RedactionList, TruncationNotice, redactionTotal } from "../components/DiagnosticsPanels";
import { Button, Callout, ConfirmDialog, EmptyState, ErrorState, Field, LinkButton, LoadingBlock, Panel, Tabs } from "../components/ui";
import { fmtBytes, fmtInt } from "../lib/format";
import { forgetImport } from "../lib/lastImport";
import { useAsync } from "../lib/useAsync";
import type { AsyncState } from "../lib/useAsync";
import { useAuth, useTime, useToast } from "../state/app";
import { useDocTitle } from "./common";

const SOFT_LIMIT = 25 * 1024 * 1024;

type Phase = { name: "idle" } | { name: "uploading"; loaded: number; total: number } | { name: "processing" } | { name: "error"; message: string; status: number };

interface Result {
  id: number;
  duplicate: boolean;
}

export function ImportView() {
  useDocTitle("Import");
  const [params, setParams] = useSearchParams();
  const { push } = useToast();
  const auth = useAuth();
  const [file, setFile] = useState<File | null>(null);
  const [service, setService] = useState("");
  const [format, setFormat] = useState("");
  const [phase, setPhase] = useState<Phase>({ name: "idle" });
  const [result, setResult] = useState<Result | null>(null);
  const [drag, setDrag] = useState(false);
  const [fileProblem, setFileProblem] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const abortRef = useRef<(() => void) | null>(null);
  const resultRef = useRef<HTMLDivElement>(null);
  const serviceId = useId();
  const formatId = useId();
  const dropId = useId();

  const imports = useAsync<Page<ImportSummary>>("imports:200", (s) => api.get<Page<ImportSummary>>("/imports", { limit: 200 }, s));

  const detailId = result?.id ?? (Number(params.get("import")) || null);
  const detail = useAsync<ImportDetail>(detailId === null ? null : `import:${detailId}`, (s) => api.get<ImportDetail>(`/imports/${detailId}`, undefined, s));

  useEffect(() => {
    if (detail.data && resultRef.current && result) resultRef.current.focus({ preventScroll: false });
  }, [detail.data, result]);

  const busy = phase.name === "uploading" || phase.name === "processing";

  const pick = (f: File | null | undefined) => {
    if (!f || busy) return;
    setResult(null);
    setPhase({ name: "idle" });
    setFile(f);
    setFileProblem(f.size === 0 ? "This file is empty. Choose a file that contains log lines." : null);
  };

  const onDrop = (e: DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setDrag(false);
    const files = e.dataTransfer.files;
    if (files.length > 1) push("Only one file can be imported at a time. Using the first.", "info");
    pick(files[0]);
  };

  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (!file || file.size === 0 || busy) return;
    setResult(null);
    setPhase({ name: "uploading", loaded: 0, total: file.size });
    const up = uploadLog(file, { service: service.trim() || undefined, format: format || undefined }, {
      onProgress: (loaded, total) => setPhase({ name: "uploading", loaded, total }),
      onUploaded: () => setPhase({ name: "processing" }),
    });
    abortRef.current = up.abort;
    up.promise
      .then(({ summary, duplicate }) => {
        setPhase({ name: "idle" });
        setResult({ id: summary.id, duplicate });
        setParams({}, { replace: true });
        imports.reload();
        push(duplicate ? "This file was already imported." : `Imported ${summary.filename}.`);
      })
      .catch((err: unknown) => {
        if (err instanceof ApiError && err.status === 401) {
          setPhase({ name: "error", status: 401, message: "An API key is required to import files." });
          return;
        }
        const e2 = err instanceof ApiError ? err : new ApiError(0, "Upload failed.");
        setPhase({ name: "error", status: e2.status, message: friendlyUploadError(e2) });
      })
      .finally(() => {
        abortRef.current = null;
      });
  };

  useEffect(() => () => abortRef.current?.(), []);

  const pct = phase.name === "uploading" && phase.total > 0 ? Math.round((phase.loaded / phase.total) * 100) : 0;
  const status =
    phase.name === "uploading" ? `Uploading ${file?.name ?? ""}: ${pct}% (${fmtBytes(phase.loaded)} of ${fmtBytes(phase.total)})` : phase.name === "processing" ? "Upload complete. Parsing the log and grouping errors on the server..." : "";

  return (
    <>
      <div className="page-head">
        <div>
          <h1 className="page-title">Import a log</h1>
          <p className="page-sub">Python, Node.js, Java Spring, JSON lines or CSV. Credentials are masked while parsing.</p>
        </div>
      </div>

      <Panel title="Choose a file">
        <form onSubmit={submit} className="stack-md" noValidate>
          <div
            className={`dropzone${drag ? " dropzone-active" : ""}${file ? " dropzone-has-file" : ""}`}
            onDragEnter={(e) => {
              e.preventDefault();
              setDrag(true);
            }}
            onDragOver={(e) => {
              e.preventDefault();
              e.dataTransfer.dropEffect = "copy";
              setDrag(true);
            }}
            onDragLeave={(e) => {
              if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setDrag(false);
            }}
            onDrop={onDrop}
            data-testid="dropzone"
          >
            <svg viewBox="0 0 48 48" width="36" height="36" aria-hidden="true" focusable="false" className="dropzone-art">
              <path d="M24 32V10M16 18l8-8 8 8" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" />
              <path d="M8 30v8h32v-8" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
            <p className="dropzone-text" id={dropId}>
              {file ? (
                <>
                  <span className="mono strong">{file.name}</span> <span className="muted">({fmtBytes(file.size)})</span>
                </>
              ) : (
                "Drag a log file here, or"
              )}
            </p>
            <input ref={inputRef} type="file" className="sr-only-input" tabIndex={-1} aria-hidden="true" onChange={(e) => pick(e.target.files?.[0])} data-testid="file-input" />
            <Button onClick={() => inputRef.current?.click()} aria-describedby={dropId} disabled={busy}>
              {file ? "Choose a different file" : "Choose file"}
            </Button>
          </div>

          {fileProblem ? (
            <Callout kind="error" title="Cannot import this file">
              {fileProblem}
            </Callout>
          ) : null}
          {file && file.size > SOFT_LIMIT ? (
            <Callout kind="warning" title="Large file">
              This file is {fmtBytes(file.size)}. The default server limit is {fmtBytes(SOFT_LIMIT)}; the upload will be rejected if your server uses the default.
            </Callout>
          ) : null}

          <div className="form-row">
            <Field label="Service name (optional)" htmlFor={serviceId} hint="Used when the log does not name its own service.">
              <input id={serviceId} className="input" value={service} maxLength={100} onChange={(e) => setService(e.target.value)} placeholder="e.g. checkout-api" autoComplete="off" disabled={busy} />
            </Field>
            <Field label="Format" htmlFor={formatId} hint="Detected automatically unless you override it.">
              <select id={formatId} className="select" value={format} onChange={(e) => setFormat(e.target.value)} disabled={busy}>
                <option value="">Detect automatically</option>
                {LOG_FORMATS.map((f) => (
                  <option key={f} value={f}>
                    {f}
                  </option>
                ))}
              </select>
            </Field>
          </div>

          <div className="form-actions">
            <Button type="submit" variant="primary" disabled={!file || file.size === 0 || busy} busy={busy}>
              {busy ? "Importing" : "Upload and analyse"}
            </Button>
            {phase.name === "uploading" ? (
              <Button onClick={() => abortRef.current?.()}>Cancel upload</Button>
            ) : null}
          </div>

          <div className="upload-status" role="status" aria-live="polite">
            {busy ? (
              <>
                <progress className="progress" max={phase.name === "uploading" ? 100 : undefined} value={phase.name === "uploading" ? pct : undefined} aria-label="Upload progress" />
                <p className="small">{status}</p>
              </>
            ) : null}
          </div>
          {phase.name === "error" ? (
            <Callout kind="error" title={phase.status === 0 ? "Upload failed" : `Import failed${phase.status ? ` (${phase.status})` : ""}`}>
              {phase.message}
              {phase.status === 401 ? (
                <>
                  {" "}
                  <Button size="sm" onClick={auth.openDialog}>
                    Enter API key
                  </Button>
                </>
              ) : null}
            </Callout>
          ) : null}
        </form>
      </Panel>

      {detailId !== null ? (
        <div ref={resultRef} tabIndex={-1} className="result-anchor" aria-label="Ingestion summary region">
          {detail.error ? (
            <ErrorState title="Could not load the ingestion summary" error={detail.error} onRetry={detail.reload} />
          ) : !detail.data ? (
            <LoadingBlock label="Loading ingestion summary" />
          ) : (
            <IngestionSummary d={detail.data} duplicate={result?.duplicate ?? false} />
          )}
        </div>
      ) : null}

      <PreviousImports
        state={imports}
        onShow={(id) => {
          setResult(null);
          setParams({ import: String(id) }, { replace: true });
          window.scrollTo({ top: 0 });
        }}
        onDeleted={(id) => {
          forgetImport(id);
          if (detailId === id) {
            setResult(null);
            setParams({}, { replace: true });
          }
        }}
      />
    </>
  );
}

function friendlyUploadError(e: ApiError): string {
  switch (e.status) {
    case 413:
      return `The file is too large for this server. ${e.message}`;
    case 415:
      return `This file is not a supported log format. ${e.message}`;
    case 400:
      return `The upload was rejected: ${e.message}`;
    case 422:
      return `Some options were not valid: ${e.message}`;
    default:
      return e.message;
  }
}

function IngestionSummary({ d, duplicate }: { d: ImportDetail; duplicate: boolean }) {
  const { fmt } = useTime();
  const [tab, setTab] = useState("diag");
  const issueTotal = Object.values(d.diagnostics.code_counts ?? {}).reduce((x, y) => x + y, 0);
  const episodes = d.anomaly && !d.anomaly.error ? (d.anomaly.episodes?.length ?? 0) : null;
  return (
    <Panel title="Ingestion summary" className="result-panel">
      {duplicate ? (
        <Callout kind="info" title="Already imported">
          This exact file was imported before ({fmt.dateTime(d.created_at)}), so nothing new was added. Showing the existing import.{" "}
          <Link to={`/?import=${d.id}`}>Open it in Overview</Link>.
        </Callout>
      ) : null}
      <TruncationNotice d={d.diagnostics} />
      <p className="result-file">
        <span className="mono strong">{d.filename}</span> <span className="muted">&middot; {fmtBytes(d.size_bytes)} &middot; import #{d.id}</span>
      </p>
      <dl className="stat-strip">
        <div className="stat">
          <dt>Format detected</dt>
          <dd className="mono">{d.format}</dd>
        </div>
        <div className="stat">
          <dt>Events parsed</dt>
          <dd className="num">{fmtInt(d.diagnostics.events_parsed ?? d.event_count)}</dd>
          <dd className="stat-hint">{d.diagnostics.lines_read !== undefined ? `${fmtInt(d.diagnostics.lines_read)} lines read` : ""}</dd>
        </div>
        <div className="stat">
          <dt>Error groups</dt>
          <dd className="num">{fmtInt(d.incident_count)}</dd>
          {episodes !== null ? <dd className="stat-hint">{episodes} unusual period{episodes === 1 ? "" : "s"}</dd> : null}
        </div>
        <div className="stat stat-wide">
          <dt>Time range ({fmt.label})</dt>
          <dd className="num small-val">
            {d.first_event ? (
              <>
                {fmt.dateTime(d.first_event).replace(` ${fmt.label}`, "")}
                <span className="muted"> to </span>
                {fmt.dateTime(d.last_event).replace(` ${fmt.label}`, "")}
              </>
            ) : (
              "no timestamps found"
            )}
          </dd>
          {d.diagnostics.events_without_timestamp ? <dd className="stat-hint">{fmtInt(d.diagnostics.events_without_timestamp)} events without a timestamp</dd> : null}
        </div>
      </dl>
      <div className="result-actions">
        <LinkButton to={`/?import=${d.id}`} variant="primary">
          Open Overview
        </LinkButton>
        <LinkButton to={`/imports/${d.id}/incidents`}>View {fmtInt(d.incident_count)} incident{d.incident_count === 1 ? "" : "s"}</LinkButton>
      </div>
      <Tabs
        label="Ingestion details"
        active={tab}
        onChange={setTab}
        tabs={[
          { id: "diag", label: `Parse diagnostics (${fmtInt(issueTotal)})`, panel: <DiagnosticsTable d={d.diagnostics} /> },
          { id: "redact", label: `Redactions (${fmtInt(redactionTotal(d.diagnostics))})`, panel: <RedactionList d={d.diagnostics} /> },
          ...(d.notes.length > 0
            ? [
                {
                  id: "notes",
                  label: `Notes (${d.notes.length})`,
                  panel: (
                    <ul className="plain-list notes-list">
                      {d.notes.map((n, i) => (
                        <li key={i}>{n}</li>
                      ))}
                    </ul>
                  ),
                },
              ]
            : []),
        ]}
      />
    </Panel>
  );
}

function PreviousImports({ state, onShow, onDeleted }: { state: AsyncState<Page<ImportSummary>>; onShow: (id: number) => void; onDeleted: (id: number) => void }) {
  const { fmt } = useTime();
  const { push } = useToast();
  const [target, setTarget] = useState<ImportSummary | null>(null);
  const [deleting, setDeleting] = useState(false);
  const items = state.data?.items ?? [];

  const confirmDelete = async () => {
    if (!target) return;
    setDeleting(true);
    try {
      await api.del(`/imports/${target.id}`);
      push(`Deleted ${target.filename}.`);
      onDeleted(target.id);
      setTarget(null);
      state.reload();
    } catch (e) {
      push(e instanceof Error ? e.message : "Could not delete the import.", "error");
    } finally {
      setDeleting(false);
    }
  };

  return (
    <Panel title="Previous imports">
      {state.error ? (
        <ErrorState title="Could not load imports" error={state.error} onRetry={state.reload} />
      ) : !state.data ? (
        <LoadingBlock label="Loading imports" />
      ) : items.length === 0 ? (
        <EmptyState title="No imports yet">Files you import will be listed here.</EmptyState>
      ) : (
        <div className="table-wrap">
          <table className="table table-stack" data-testid="imports-table">
            <caption className="sr-only">Previous imports</caption>
            <thead>
              <tr>
                <th scope="col">File</th>
                <th scope="col" className="num">
                  Size
                </th>
                <th scope="col" className="num">
                  Events
                </th>
                <th scope="col" className="num">
                  Groups
                </th>
                <th scope="col">Imported ({fmt.label})</th>
                <th scope="col">
                  <span className="sr-only">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {items.map((i) => (
                <tr key={i.id}>
                  <td data-label="File" className="cell-primary">
                    <span className="mono ellipsis" title={i.filename}>
                      {i.filename}
                    </span>
                    <span className="muted small"> {i.format}{i.truncated ? " (truncated)" : ""}</span>
                  </td>
                  <td data-label="Size" className="num">
                    {fmtBytes(i.size_bytes)}
                  </td>
                  <td data-label="Events" className="num">
                    {fmtInt(i.event_count)}
                  </td>
                  <td data-label="Groups" className="num">
                    {fmtInt(i.incident_count)}
                  </td>
                  <td data-label={`Imported (${fmt.label})`} className="nowrap">
                    {fmt.dateTimeBare(i.created_at)}
                  </td>
                  <td className="cell-actions">
                    <Button size="sm" onClick={() => onShow(i.id)} aria-label={`Show ingestion summary for ${i.filename}`}>
                      Summary
                    </Button>
                    <LinkButton to={`/?import=${i.id}`} size="sm">
                      Overview
                    </LinkButton>
                    <Button size="sm" variant="danger" onClick={() => setTarget(i)} aria-label={`Delete ${i.filename}`}>
                      Delete
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <ConfirmDialog
        open={target !== null}
        title="Delete this import?"
        body={
          <p>
            <span className="mono strong">{target?.filename}</span> and its {target ? fmtInt(target.incident_count) : 0} error groups will be removed. Review status and notes you added are stored per error fingerprint and stay available if the same error is imported again.
          </p>
        }
        confirmLabel="Delete import"
        onConfirm={confirmDelete}
        onCancel={() => setTarget(null)}
        busy={deleting}
      />
    </Panel>
  );
}
