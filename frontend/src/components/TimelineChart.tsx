import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
import type { KeyboardEvent, PointerEvent, RefObject } from "react";
import { useTime } from "../state/app";
import { fmtNum } from "../lib/format";

export interface ChartSeries {
  key: string;
  label: string;
  /** css class selecting the fill, e.g. "c-error" */
  tone: string;
}

export interface ChartBucket {
  startMs: number;
  /** same order as `series` */
  values: number[];
}

export interface ChartBand {
  startMs: number;
  endMs: number;
  label: string;
}

export interface TooltipContent {
  title: string;
  rows: { label: string; value: string; tone?: string }[];
  note?: string;
}

interface Props {
  buckets: ChartBucket[];
  stepMs: number;
  series: ChartSeries[];
  bands?: ChartBand[];
  /** faint reference bars drawn behind the series, scaled independently */
  context?: number[];
  kind?: "bars" | "line";
  height?: number;
  ariaLabel: string;
  unit: string;
  tooltip: (i: number) => TooltipContent;
  yFormat?: (v: number) => string;
}

const M = { left: 46, right: 10, top: 10, bottom: 34 };

function niceMax(v: number): number {
  if (v <= 0) return 1;
  const pow = 10 ** Math.floor(Math.log10(v));
  const f = v / pow;
  const nice = f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10;
  return nice * pow;
}

function useWidth(ref: RefObject<HTMLElement | null>): number {
  const [w, setW] = useState(720);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver((entries) => {
      const cw = entries[0]?.contentRect.width;
      if (cw && cw > 0) setW(Math.floor(cw));
    });
    ro.observe(el);
    setW(Math.max(200, Math.floor(el.getBoundingClientRect().width) || 720));
    return () => ro.disconnect();
  }, [ref]);
  return w;
}

export function TimelineChart({ buckets, stepMs, series, bands = [], context, kind = "bars", height = 240, ariaLabel, unit, tooltip, yFormat = fmtNum }: Props) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const width = useWidth(wrapRef);
  const { fmt } = useTime();
  const [active, setActive] = useState<number | null>(null);
  const [kbd, setKbd] = useState(false);
  const descId = useId();
  const n = buckets.length;

  const plotW = Math.max(40, width - M.left - M.right);
  const plotH = height - M.top - M.bottom;
  const t0 = buckets[0]?.startMs ?? 0;
  const span = Math.max(stepMs, n * stepMs);

  const totals = useMemo(() => buckets.map((b) => b.values.reduce((a, v) => a + v, 0)), [buckets]);
  const yMax = useMemo(() => niceMax(Math.max(0, ...(kind === "line" ? buckets.flatMap((b) => b.values).filter((v) => Number.isFinite(v)) : totals))), [buckets, totals, kind]);
  const ctxMax = useMemo(() => (context ? Math.max(1, ...context) : 1), [context]);

  const x = useCallback((ms: number) => M.left + ((ms - t0) / span) * plotW, [t0, span, plotW]);
  const y = useCallback((v: number) => M.top + plotH - (v / yMax) * plotH, [plotH, yMax]);
  const slot = plotW / Math.max(1, n);
  const barW = Math.max(1, Math.min(28, slot - (slot > 4 ? 1.5 : 0.5)));

  const yTicks = useMemo(() => (kind === "bars" && yMax <= 5 ? Array.from({ length: yMax + 1 }, (_, i) => i) : [0, yMax / 2, yMax]), [yMax, kind]);
  const xTicks = useMemo(() => {
    const target = Math.max(2, Math.floor(plotW / 96));
    const every = Math.max(1, Math.ceil(n / target));
    const out: { i: number; label: string; sub?: string }[] = [];
    let lastDate = "";
    for (let i = 0; i < n; i += every) {
      const ms = buckets[i]!.startMs;
      const d = fmt.date(ms);
      out.push({ i, label: fmt.hm(ms), sub: d !== lastDate ? d : undefined });
      lastDate = d;
    }
    return out;
  }, [buckets, fmt, n, plotW]);

  const indexAt = (clientX: number, el: SVGSVGElement): number => {
    const rect = el.getBoundingClientRect();
    const px = clientX - rect.left - M.left;
    return Math.min(n - 1, Math.max(0, Math.floor((px / plotW) * n)));
  };
  const onMove = (e: PointerEvent<SVGSVGElement>) => {
    setKbd(false);
    const rect = e.currentTarget.getBoundingClientRect();
    const px = e.clientX - rect.left;
    if (px < M.left || px > rect.width - M.right) {
      setActive(null);
      return;
    }
    setActive(indexAt(e.clientX, e.currentTarget));
  };
  const onKey = (e: KeyboardEvent<HTMLDivElement>) => {
    if (n === 0) return;
    let next: number | null = null;
    const cur = active ?? -1;
    if (e.key === "ArrowRight") next = Math.min(n - 1, cur + 1);
    else if (e.key === "ArrowLeft") next = Math.max(0, cur < 0 ? 0 : cur - 1);
    else if (e.key === "Home") next = 0;
    else if (e.key === "End") next = n - 1;
    else if (e.key === "PageDown") next = Math.min(n - 1, cur + 10);
    else if (e.key === "PageUp") next = Math.max(0, cur - 10);
    else if (e.key === "Escape") {
      setActive(null);
      return;
    }
    if (next !== null) {
      e.preventDefault();
      setKbd(true);
      setActive(next);
    }
  };

  const tip = active !== null && active < n ? tooltip(active) : null;
  const tipLeft = active !== null ? x(buckets[active]!.startMs) + slot / 2 : 0;
  const flip = tipLeft > width * 0.6;

  const lineSeries = useMemo(() => {
    if (kind !== "line") return [];
    return series.map((s, si) => {
      let d = "";
      let pen = false;
      buckets.forEach((b) => {
        const v = b.values[si];
        if (v === undefined || !Number.isFinite(v)) {
          pen = false;
          return;
        }
        d += `${pen ? "L" : "M"}${(x(b.startMs) + slot / 2).toFixed(1)},${y(v).toFixed(1)}`;
        pen = true;
      });
      return { s, d };
    });
  }, [kind, series, buckets, x, y, slot]);

  const nonEmpty = buckets.map((b, i) => ({ b, i })).filter(({ b }) => b.values.some((v) => v > 0 || Number.isFinite(v) && kind === "line"));

  return (
    <div className="chart" ref={wrapRef}>
      <div
        className="chart-focus"
        role="group"
        tabIndex={0}
        aria-label={`${ariaLabel}. Use the left and right arrow keys to inspect each time window.`}
        aria-describedby={descId}
        onKeyDown={onKey}
        onFocus={() => {
          if (active === null && n > 0) {
            setKbd(true);
            setActive(0);
          }
        }}
        onBlur={() => kbd && setActive(null)}
        onPointerLeave={() => !kbd && setActive(null)}
      >
        <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} aria-hidden="true" focusable="false" onPointerMove={onMove} className="chart-svg">
          <defs>
            <pattern id={`${descId}-hatch`} width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
              <rect width="6" height="6" fill="var(--band-bg)" />
              <line x1="0" y1="0" x2="0" y2="6" stroke="var(--band-line)" strokeWidth="1.5" />
            </pattern>
          </defs>
          {/* y grid + labels */}
          {yTicks.map((t, i) => (
            <g key={i}>
              <line className="chart-grid" x1={M.left} x2={width - M.right} y1={y(t)} y2={y(t)} />
              <text className="chart-tick" x={M.left - 6} y={y(t)} textAnchor="end" dominantBaseline="middle">
                {yFormat(t)}
              </text>
            </g>
          ))}
          {/* bands */}
          {bands.map((b, i) => {
            const x0 = Math.max(M.left, x(b.startMs));
            const x1 = Math.min(width - M.right, x(b.endMs));
            return (
              <g key={i} className="chart-band">
                <rect x={x0} y={M.top} width={Math.max(2, x1 - x0)} height={plotH} fill={`url(#${descId}-hatch)`} />
                <rect x={x0} y={M.top + plotH + 2} width={Math.max(2, x1 - x0)} height={4} className="chart-band-mark" />
              </g>
            );
          })}
          {/* context */}
          {context
            ? context.map((v, i) =>
                v > 0 ? <rect key={i} className="chart-context" x={x(buckets[i]!.startMs) + (slot - barW) / 2} width={barW} y={M.top + plotH - (v / ctxMax) * plotH * 0.9} height={(v / ctxMax) * plotH * 0.9} /> : null,
              )
            : null}
          {/* bars */}
          {kind === "bars"
            ? buckets.map((b, i) => {
                let acc = 0;
                const bx = x(b.startMs) + (slot - barW) / 2;
                return (
                  <g key={i}>
                    {b.values.map((v, si) => {
                      if (!(v > 0)) return null;
                      const y1 = y(acc + v);
                      const h = y(acc) - y1;
                      acc += v;
                      return <rect key={si} className={`chart-bar ${series[si]?.tone ?? ""}`} x={bx} y={y1} width={barW} height={Math.max(1, h)} />;
                    })}
                  </g>
                );
              })
            : lineSeries.map(({ s, d }) => <path key={s.key} d={d} className={`chart-line ${s.tone}`} fill="none" />)}
          {/* baseline */}
          <line className="chart-axis" x1={M.left} x2={width - M.right} y1={M.top + plotH} y2={M.top + plotH} />
          {xTicks.map((t) => {
            const tx = x(buckets[t.i]!.startMs) + slot / 2;
            return (
              <g key={t.i}>
                <line className="chart-axis" x1={tx} x2={tx} y1={M.top + plotH} y2={M.top + plotH + 4} />
                <text className="chart-tick" x={tx} y={M.top + plotH + 17} textAnchor="middle">
                  {t.label}
                </text>
                {t.sub ? (
                  <text className="chart-tick chart-tick-sub" x={tx} y={M.top + plotH + 29} textAnchor="middle">
                    {t.sub}
                  </text>
                ) : null}
              </g>
            );
          })}
          {/* cursor */}
          {active !== null && buckets[active] ? (
            <g>
              <rect className="chart-cursor" x={x(buckets[active].startMs)} y={M.top} width={Math.max(2, slot)} height={plotH} />
              {kind === "line"
                ? series.map((s, si) => <circle key={s.key} className={`chart-dot ${s.tone}`} cx={x(buckets[active]!.startMs) + slot / 2} cy={y(buckets[active]!.values[si] ?? 0)} r="3.5" />)
                : null}
            </g>
          ) : null}
        </svg>
        {tip ? (
          <div className={`chart-tip${flip ? " chart-tip-left" : ""}`} style={{ left: tipLeft, top: M.top }}>
            <div className="chart-tip-title">{tip.title}</div>
            {tip.rows.map((r, i) => (
              <div className="chart-tip-row" key={i}>
                <span className={`chart-tip-swatch ${r.tone ?? ""}`} aria-hidden="true" />
                <span>{r.label}</span>
                <span className="chart-tip-val">{r.value}</span>
              </div>
            ))}
            {tip.note ? <div className="chart-tip-note">{tip.note}</div> : null}
          </div>
        ) : null}
      </div>
      <div className="sr-only" id={descId} aria-live={kbd ? "polite" : "off"}>
        {tip ? `${tip.title}. ${tip.rows.map((r) => `${r.label} ${r.value}`).join(", ")}. ${tip.note ?? ""}` : `${n} time windows. ${unit}.`}
      </div>
      <div className="sr-only">
      <table>
        <caption>{ariaLabel}</caption>
        <thead>
          <tr>
            <th scope="col">Window start</th>
            {series.map((s) => (
              <th scope="col" key={s.key}>
                {s.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {nonEmpty.slice(0, 300).map(({ b, i }) => (
            <tr key={i}>
              <th scope="row">{fmt.dateTime(b.startMs)}</th>
              {b.values.map((v, si) => (
                <td key={si}>{Number.isFinite(v) ? fmtNum(v) : "no data"}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      </div>
      <ChartLegend series={series} hasBands={bands.length > 0} hasContext={Boolean(context)} />
    </div>
  );
}

function ChartLegend({ series, hasBands, hasContext }: { series: ChartSeries[]; hasBands: boolean; hasContext: boolean }) {
  return (
    <ul className="legend" aria-label="Chart legend">
      {series.map((s) => (
        <li key={s.key}>
          <span className={`legend-swatch ${s.tone}`} aria-hidden="true" />
          {s.label}
        </li>
      ))}
      {hasContext ? (
        <li>
          <span className="legend-swatch chart-context" aria-hidden="true" />
          Import-wide errors (own scale)
        </li>
      ) : null}
      {hasBands ? (
        <li>
          <span className="legend-swatch legend-band" aria-hidden="true" />
          Unusual period
        </li>
      ) : null}
    </ul>
  );
}
