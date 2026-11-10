import type { Diagnostics } from "../api/types";
import { fmtInt } from "../lib/format";
import { Callout, Panel } from "./ui";

const CODE_HELP: Record<string, string> = {
  ORPHAN_LINE: "Lines before the first recognisable record were skipped",
  MISSING_TIMESTAMP: "Event has no usable timestamp, so it cannot appear on the timeline",
  BAD_TIMESTAMP: "Timestamp text could not be parsed",
  TIMEZONE_ASSUMED_UTC: "No timezone in the log; times are treated as UTC",
  INVALID_JSON: "Line is not valid JSON and was skipped",
  NOT_AN_OBJECT: "JSON line was not an object and was skipped",
  LINE_TOO_LONG: "Very long line was cut short",
  STACK_TRUNCATED: "Long stack trace had its middle omitted",
  MISSING_LEVEL: "No level on the record",
  UNKNOWN_LEVEL: "Level value was not recognised",
  MISSING_MESSAGE: "Record has no message",
  BAD_ROW_WIDTH: "CSV row does not match the header width and was skipped",
  FILE_TOO_LARGE: "File exceeded the size limit; the rest was ignored",
  EVENT_LIMIT: "Event limit reached; the rest was ignored",
  NO_EVENTS: "Nothing could be parsed from this file",
};

export function redactionTotal(d: Diagnostics): number {
  return Object.values(d.redactions ?? {}).reduce((a, v) => a + v, 0);
}

export function TruncationNotice({ d }: { d: Diagnostics }) {
  if (!d.truncated) return null;
  return (
    <Callout kind="warning" title="Import was truncated">
      {d.truncated_reason ?? "Only part of the file was analysed."} Counts below cover the part that was read; later events are not included.
    </Callout>
  );
}

export function RedactionList({ d }: { d: Diagnostics }) {
  const entries = Object.entries(d.redactions ?? {}).sort((a, b) => b[1] - a[1]);
  if (entries.length === 0) return <p className="muted">No credentials or personal data patterns were masked.</p>;
  return (
    <>
      <p className="muted">Sensitive values are masked when the log is parsed, before anything is stored.</p>
      <ul className="kv-list">
        {entries.map(([k, v]) => (
          <li key={k}>
            <span className="mono">{k}</span>
            <span className="num">{fmtInt(v)}</span>
          </li>
        ))}
      </ul>
    </>
  );
}

export function DiagnosticsTable({ d }: { d: Diagnostics }) {
  const counts = Object.entries(d.code_counts ?? {}).sort((a, b) => b[1] - a[1]);
  const issues = d.issues ?? [];
  if (counts.length === 0 && issues.length === 0) return <p className="muted">The parser reported no problems with this file.</p>;
  return (
    <div className="table-wrap">
      <table className="table table-stack">
        <caption className="sr-only">Parse diagnostics</caption>
        <thead>
          <tr>
            <th scope="col">Code</th>
            <th scope="col" className="num">
              Count
            </th>
            <th scope="col">Examples (line number)</th>
          </tr>
        </thead>
        <tbody>
          {counts.map(([code, n]) => {
            const ex = issues.filter((i) => i.code === code).slice(0, 3);
            return (
              <tr key={code}>
                <td data-label="Code">
                  <span className="mono">{code}</span>
                  {CODE_HELP[code] ? <div className="muted small">{CODE_HELP[code]}</div> : null}
                </td>
                <td data-label="Count" className="num">
                  {fmtInt(n)}
                </td>
                <td data-label="Examples">
                  {ex.length === 0 ? (
                    <span className="muted">none recorded</span>
                  ) : (
                    <ul className="plain-list">
                      {ex.map((i, k) => (
                        <li key={k}>
                          <span className="mono line-no">{i.line === null ? "-" : `L${fmtInt(i.line)}`}</span> <span>{i.message}</span>
                        </li>
                      ))}
                    </ul>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export function DiagnosticsSummaryPanel({ d, notes }: { d: Diagnostics; notes?: string[] }) {
  const issueTotal = Object.values(d.code_counts ?? {}).reduce((a, v) => a + v, 0);
  return (
    <Panel title="Parse diagnostics">
      <TruncationNotice d={d} />
      <dl className="stat-list">
        <div>
          <dt>Lines read</dt>
          <dd className="num">{d.lines_read !== undefined ? fmtInt(d.lines_read) : "-"}</dd>
        </div>
        <div>
          <dt>Events parsed</dt>
          <dd className="num">{d.events_parsed !== undefined ? fmtInt(d.events_parsed) : "-"}</dd>
        </div>
        <div>
          <dt>Without timestamp</dt>
          <dd className="num">{d.events_without_timestamp !== undefined ? fmtInt(d.events_without_timestamp) : "-"}</dd>
        </div>
        <div>
          <dt>Parser issues</dt>
          <dd className="num">{fmtInt(issueTotal)}</dd>
        </div>
        <div>
          <dt>Values redacted</dt>
          <dd className="num">{fmtInt(redactionTotal(d))}</dd>
        </div>
      </dl>
      {notes && notes.length > 0 ? (
        <ul className="plain-list notes-list">
          {notes.map((n, i) => (
            <li key={i}>{n}</li>
          ))}
        </ul>
      ) : null}
    </Panel>
  );
}
