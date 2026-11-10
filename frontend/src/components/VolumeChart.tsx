import { useId, useMemo, useState } from "react";
import type { AnomalyReport, TimelineBucket } from "../api/types";
import { classificationLabel, fmtNum, toMs } from "../lib/format";
import { useTime } from "../state/app";
import { episodeBands } from "./Anomaly";
import { TimelineChart } from "./TimelineChart";
import type { ChartBucket, ChartSeries, TooltipContent } from "./TimelineChart";
import { Panel } from "./ui";

const ERRORS: ChartSeries[] = [{ key: "errors", label: "Errors (error + critical)", tone: "c-error" }];
const ALL: ChartSeries[] = [
  { key: "errors", label: "Errors (error + critical)", tone: "c-error" },
  { key: "warning", label: "Warnings", tone: "c-warning" },
  { key: "other", label: "Other (info, debug, unknown)", tone: "c-other" },
];

export function stepOf(timeline: TimelineBucket[], windowSeconds: number): number {
  if (timeline.length > 1) {
    const d = timeline[1]!.start_ms - timeline[0]!.start_ms;
    if (d > 0) return d;
  }
  return Math.max(1, windowSeconds) * 1000;
}

export function anomalyAt(report: AnomalyReport | null, startMs: number, index: number) {
  const w = report?.windows?.[index];
  if (w && toMs(w.start) === startMs) return w;
  return report?.windows?.find((x) => toMs(x.start) === startMs);
}

export function VolumeChart({ timeline, windowSeconds, anomaly }: { timeline: TimelineBucket[]; windowSeconds: number; anomaly: AnomalyReport | null }) {
  const [mode, setMode] = useState<"errors" | "all">("errors");
  const { fmt } = useTime();
  const step = stepOf(timeline, windowSeconds);
  const report = anomaly && !anomaly.error ? anomaly : null;
  const series = mode === "errors" ? ERRORS : ALL;
  const buckets: ChartBucket[] = useMemo(
    () =>
      timeline.map((b) => {
        const errors = b.error + b.critical;
        return { startMs: b.start_ms, values: mode === "errors" ? [errors] : [errors, b.warning, b.debug + b.info + b.unknown] };
      }),
    [timeline, mode],
  );
  const bands = useMemo(() => episodeBands(report), [report]);
  const totalErrors = timeline.reduce((a, b) => a + b.error + b.critical, 0);
  const peak = timeline.reduce<TimelineBucket | null>((p, b) => (!p || b.error + b.critical > p.error + p.critical ? b : p), null);

  const tooltip = (i: number): TooltipContent => {
    const b = timeline[i]!;
    const rows: TooltipContent["rows"] = [{ label: "Errors", value: fmtNum(b.error + b.critical), tone: "c-error" }];
    if (mode === "all") {
      rows.push({ label: "Warnings", value: fmtNum(b.warning), tone: "c-warning" }, { label: "Other", value: fmtNum(b.debug + b.info + b.unknown), tone: "c-other" });
    }
    rows.push({ label: "All events", value: fmtNum(b.total) });
    if (b.new_groups > 0) rows.push({ label: "New error groups", value: fmtNum(b.new_groups) });
    const w = anomalyAt(report, b.start_ms, i);
    let note: string | undefined;
    if (w?.is_anomalous) note = `Unusual window: ${classificationLabel(w.classification)}${w.severity ? `, severity ${w.severity}` : ""}. Anomaly score ${w.score.toFixed(2)} (0 to 1).`;
    return { title: `${fmt.dateTime(b.start_ms)} (${windowSeconds}s window)`, rows, note };
  };

  const summary = `Log volume over time, ${timeline.length} windows of ${windowSeconds} seconds. ${fmtNum(totalErrors)} errors in total${peak && peak.error + peak.critical > 0 ? `, peak ${fmtNum(peak.error + peak.critical)} at ${fmt.dateTime(peak.start_ms)}` : ""}. ${bands.length} unusual period${bands.length === 1 ? "" : "s"} shaded.`;

  return (
    <Panel
      title="Log volume over time"
      actions={
        <div className="segmented" role="group" aria-label="Chart series">
          <button type="button" aria-pressed={mode === "errors"} onClick={() => setMode("errors")}>
            Errors
          </button>
          <button type="button" aria-pressed={mode === "all"} onClick={() => setMode("all")}>
            All levels
          </button>
        </div>
      }
    >
      {timeline.length === 0 ? (
        <p className="muted">No events with timestamps, so there is no timeline to draw.</p>
      ) : (
        <>
          <TimelineChart buckets={buckets} stepMs={step} series={series} bands={bands} ariaLabel={summary} unit="events per window" tooltip={tooltip} />
          <p className="muted small chart-foot">
            Counts per {windowSeconds}-second window, times in {fmt.label}.
          </p>
        </>
      )}
    </Panel>
  );
}

export function metricNames(timeline: TimelineBucket[]): string[] {
  const names = new Set<string>();
  for (const b of timeline) for (const k of Object.keys(b.metrics ?? {})) names.add(k);
  return [...names].sort();
}

export function MetricsChart({ timeline, windowSeconds, anomaly }: { timeline: TimelineBucket[]; windowSeconds: number; anomaly: AnomalyReport | null }) {
  const names = useMemo(() => metricNames(timeline), [timeline]);
  const [name, setName] = useState<string>(() => names[0] ?? "");
  const selectId = useId();
  const { fmt } = useTime();
  const report = anomaly && !anomaly.error ? anomaly : null;
  const active = names.includes(name) ? name : (names[0] ?? "");
  const buckets: ChartBucket[] = useMemo(() => timeline.map((b) => ({ startMs: b.start_ms, values: [b.metrics?.[active] ?? Number.NaN] })), [timeline, active]);
  const bands = useMemo(() => episodeBands(report), [report]);
  if (names.length === 0) return null;
  const step = stepOf(timeline, windowSeconds);
  const tone = "c-metric";
  return (
    <Panel
      title="Metrics"
      actions={
        <div className="picker">
          <label htmlFor={selectId} className="picker-label">
            Metric
          </label>
          <select id={selectId} className="select" value={active} onChange={(e) => setName(e.target.value)}>
            {names.map((n) => (
              <option key={n} value={n}>
                {n}
              </option>
            ))}
          </select>
        </div>
      }
    >
      <TimelineChart
        kind="line"
        buckets={buckets}
        stepMs={step}
        series={[{ key: active, label: `${active} (mean per window)`, tone }]}
        bands={bands}
        ariaLabel={`Metric ${active}, mean value per ${windowSeconds}-second window`}
        unit={`mean ${active} per window`}
        tooltip={(i) => {
          const b = timeline[i]!;
          const v = b.metrics?.[active];
          const w = anomalyAt(report, b.start_ms, i);
          const c = w?.contributions?.find((x) => x.feature === `metric.${active}`);
          return {
            title: fmt.dateTime(b.start_ms),
            rows: [{ label: active, value: v === undefined ? "no samples" : fmtNum(v), tone }],
            note: c ? `Baseline ${fmtNum(c.baseline)}; deviation ${fmtNum(c.deviation)}` : w?.is_anomalous ? `Unusual window; anomaly score ${w.score.toFixed(2)} (0 to 1)` : undefined,
          };
        }}
      />
      <p className="muted small chart-foot">Mean of the numeric values recorded in each window. Windows without samples leave a gap.</p>
    </Panel>
  );
}
