import { useState } from "react";
import type { AnomalyEpisode, AnomalyReport, AnomalyWindow, Contribution } from "../api/types";
import { classificationLabel, featureLabel, fmtFeatureValue, fmtNum, toMs } from "../lib/format";
import { useTime } from "../state/app";
import { Button, Callout, ClassificationBadge, Panel, SeverityBadge } from "./ui";

export function episodeBands(report: AnomalyReport | null | undefined) {
  return (report?.episodes ?? []).flatMap((e) => {
    const s = toMs(e.start);
    const t = toMs(e.end);
    return s === null || t === null ? [] : [{ startMs: s, endMs: t, label: classificationLabel(e.classification) }];
  });
}

/** Contributions of the highest-scoring flagged window inside the episode. */
export function episodeContributions(report: AnomalyReport, ep: AnomalyEpisode): Contribution[] {
  const s = toMs(ep.start) ?? 0;
  const e = toMs(ep.end) ?? 0;
  let best: AnomalyWindow | null = null;
  for (const w of report.windows ?? []) {
    const ws = toMs(w.start);
    if (ws === null || ws < s || ws >= e || !w.is_anomalous) continue;
    if (!best || w.score > best.score) best = w;
  }
  return best?.contributions ?? [];
}

function ContributionTable({ items }: { items: Contribution[] }) {
  if (items.length === 0) return <p className="muted small">No single feature stood out for this window.</p>;
  return (
    <table className="table table-compact contrib">
      <caption className="sr-only">Features that contributed to this finding</caption>
      <thead>
        <tr>
          <th scope="col">Feature</th>
          <th scope="col" className="num">
            Observed
          </th>
          <th scope="col" className="num">
            Baseline
          </th>
          <th scope="col" className="num">
            Deviation
          </th>
        </tr>
      </thead>
      <tbody>
        {items.slice(0, 4).map((c) => (
          <tr key={c.feature}>
            <th scope="row" className="mono">
              {featureLabel(c.feature)}
            </th>
            <td className="num">{fmtFeatureValue(c.feature, c.observed)}</td>
            <td className="num">{fmtFeatureValue(c.feature, c.baseline)}</td>
            <td className="num">{`${c.deviation >= 0 ? "+" : ""}${fmtNum(c.deviation)}`}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function UnusualPeriods({ report }: { report: AnomalyReport | null }) {
  const { fmt } = useTime();
  const [all, setAll] = useState(false);
  let body;
  if (!report) {
    body = <p className="muted">Unusual-period analysis is not available for this import.</p>;
  } else if (report.error) {
    body = (
      <Callout kind="warning" title="Analysis could not run">
        {report.error}
      </Callout>
    );
  } else {
    const eps = report.episodes ?? [];
    const shown = all ? eps : eps.slice(0, 4);
    body = (
      <>
        <p className="muted small method-line">
          Statistical findings from the <strong>{report.method_used === "isolation_forest" ? "isolation forest" : (report.method_used ?? "statistical")}</strong> method
          {report.requested_method && report.requested_method !== report.method_used ? ` (requested: ${report.requested_method})` : ""}.
          {report.train_range ? ` Baseline learned from ${fmt.dateTime(report.train_range[0])} to ${fmt.dateTime(report.train_range[1])}.` : ""} These are periods that differ from the baseline, not causes.
        </p>
        {eps.length === 0 ? (
          <p className="empty-line">No unusual periods were found against the baseline.</p>
        ) : (
          <ol className="episodes">
            {shown.map((ep, i) => (
              <li key={`${ep.start}-${i}`} className="episode">
                <div className="episode-head">
                  <span className="episode-time">
                    {fmt.dateTime(ep.start)} <span className="muted">to</span> {fmt.dateTime(ep.end)}
                  </span>
                  <span className="episode-badges">
                    <ClassificationBadge value={ep.classification} />
                    <SeverityBadge value={ep.severity} />
                  </span>
                </div>
                <p className="episode-meta muted small">
                  Anomaly score (peak): <span className="num strong">{ep.peak_score.toFixed(2)}</span> on a 0 to 1 scale &middot; {ep.window_count} window{ep.window_count === 1 ? "" : "s"}
                </p>
                <ContributionTable items={episodeContributions(report, ep)} />
                {ep.evidence && ep.evidence.length > 0 ? <p className="small episode-evidence">Severity inputs: {ep.evidence.join("; ")}</p> : null}
              </li>
            ))}
          </ol>
        )}
        {eps.length > 4 ? (
          <Button size="sm" onClick={() => setAll((a) => !a)} aria-expanded={all}>
            {all ? "Show fewer" : `Show all ${eps.length} periods`}
          </Button>
        ) : null}
        {(report.notes ?? []).length > 0 ? (
          <ul className="plain-list notes-list">
            {(report.notes ?? []).map((n, i) => (
              <li key={i}>{n}</li>
            ))}
          </ul>
        ) : null}
      </>
    );
  }
  return <Panel title="Unusual periods">{body}</Panel>;
}
