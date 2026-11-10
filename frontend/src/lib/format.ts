export type TzMode = "utc" | "local";

const intFmt = new Intl.NumberFormat("en-US");
export const fmtInt = (n: number): string => intFmt.format(n);

export function fmtNum(n: number): string {
  if (!Number.isFinite(n)) return "-";
  if (Number.isInteger(n)) return intFmt.format(n);
  const abs = Math.abs(n);
  const d = abs >= 100 ? 0 : abs >= 10 ? 1 : 2;
  return new Intl.NumberFormat("en-US", { maximumFractionDigits: d }).format(n);
}

export function fmtBytes(n: number): string {
  if (n < 1024) return `${fmtInt(n)} B`;
  const units = ["KB", "MB", "GB"];
  let v = n / 1024;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i++;
  }
  return `${v.toFixed(v >= 100 ? 0 : 1)} ${units[i]}`;
}

export function fmtPct(n: number): string {
  return `${(n * 100).toFixed(1)}%`;
}

export function fmtDuration(ms: number): string {
  const s = Math.round(Math.abs(ms) / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m${s % 60 ? ` ${s % 60}s` : ""}`;
  const h = Math.floor(m / 60);
  if (h < 48) return `${h}h${m % 60 ? ` ${m % 60}m` : ""}`;
  const d = Math.floor(h / 24);
  return `${d}d${h % 24 ? ` ${h % 24}h` : ""}`;
}

export function toMs(v: string | number | Date | null | undefined): number | null {
  if (v === null || v === undefined) return null;
  if (typeof v === "number") return v;
  const t = v instanceof Date ? v.getTime() : Date.parse(v);
  return Number.isNaN(t) ? null : t;
}

const cache = new Map<string, Intl.DateTimeFormat>();
function dtf(tz: TzMode, opts: Intl.DateTimeFormatOptions): Intl.DateTimeFormat {
  const key = tz + JSON.stringify(opts);
  let f = cache.get(key);
  if (!f) {
    f = new Intl.DateTimeFormat("en-US", { ...opts, hourCycle: "h23", ...(tz === "utc" ? { timeZone: "UTC" } : {}) });
    cache.set(key, f);
  }
  return f;
}

function parts(ms: number, tz: TzMode, opts: Intl.DateTimeFormatOptions): Record<string, string> {
  const out: Record<string, string> = {};
  for (const p of dtf(tz, opts).formatToParts(ms)) out[p.type] = p.value;
  return out;
}

export function tzLabel(tz: TzMode, ms?: number): string {
  if (tz === "utc") return "UTC";
  return parts(ms ?? Date.now(), "local", { timeZoneName: "short" }).timeZoneName ?? "local";
}

const DATE_OPTS: Intl.DateTimeFormatOptions = { year: "numeric", month: "2-digit", day: "2-digit" };
const TIME_OPTS: Intl.DateTimeFormatOptions = { hour: "2-digit", minute: "2-digit", second: "2-digit" };

type TimeInput = string | number | null | undefined;

export interface TimeFmt {
  tz: TzMode;
  label: string;
  /** 2026-03-05 10:15:00 UTC */
  dateTime: (v: TimeInput) => string;
  /** 2026-03-05 10:15:00 (no zone label; use under a labelled header) */
  dateTimeBare: (v: TimeInput) => string;
  /** 2026-03-05 10:15:00.123 UTC */
  dateTimeMs: (v: TimeInput) => string;
  /** 10:15 */
  hm: (v: TimeInput) => string;
  /** 10:15:00 */
  hms: (v: TimeInput) => string;
  /** 2026-03-05 */
  date: (v: TimeInput) => string;
  /** 03-05 10:15 */
  short: (v: TimeInput) => string;
}

export function makeTimeFmt(tz: TzMode): TimeFmt {
  const date = (ms: number) => {
    const p = parts(ms, tz, DATE_OPTS);
    return `${p.year}-${p.month}-${p.day}`;
  };
  const hms = (ms: number) => {
    const p = parts(ms, tz, TIME_OPTS);
    return `${p.hour}:${p.minute}:${p.second}`;
  };
  const lab = (ms: number) => tzLabel(tz, ms);
  const wrap =
    (fn: (ms: number) => string) =>
    (v: TimeInput): string => {
      const ms = toMs(v);
      return ms === null ? "-" : fn(ms);
    };
  return {
    tz,
    label: tzLabel(tz),
    dateTime: wrap((ms) => `${date(ms)} ${hms(ms)} ${lab(ms)}`),
    dateTimeBare: wrap((ms) => `${date(ms)} ${hms(ms)}`),
    dateTimeMs: wrap((ms) => `${date(ms)} ${hms(ms)}.${String(((ms % 1000) + 1000) % 1000).padStart(3, "0")} ${lab(ms)}`),
    hm: wrap((ms) => hms(ms).slice(0, 5)),
    hms: wrap(hms),
    date: wrap(date),
    short: wrap((ms) => `${date(ms).slice(5)} ${hms(ms).slice(0, 5)}`),
  };
}

export function recurrenceLabel(recurrence: string, periodSeconds: number | null): string {
  switch (recurrence) {
    case "periodic":
      return periodSeconds ? `periodic every ${fmtDuration(periodSeconds * 1000)}` : "periodic";
    case "bursty":
      return "bursty";
    case "irregular":
      return "irregular";
    case "single":
      return "single occurrence";
    default:
      return recurrence;
  }
}

export function fmtRate(perMinute: number | null): string {
  if (perMinute === null || perMinute === undefined) return "-";
  if (perMinute >= 10) return `${perMinute.toFixed(0)}/min`;
  if (perMinute >= 1) return `${perMinute.toFixed(1)}/min`;
  return `${perMinute.toFixed(2)}/min`;
}

const CLASS_LABELS: Record<string, string> = {
  normal: "Normal",
  spike: "Spike",
  sustained_shift: "Sustained shift",
  new_error_type: "New error type",
  quiet_period: "Quiet period",
  unusual_pattern: "Unusual pattern",
};
export function classificationLabel(c: string): string {
  return CLASS_LABELS[c] ?? c.replace(/_/g, " ");
}

export function featureLabel(f: string): string {
  if (f.startsWith("metric.")) return `metric ${f.slice(7)}`;
  return f.replace(/_/g, " ");
}

export function fmtFeatureValue(feature: string, v: number): string {
  if (feature === "error_ratio") return fmtPct(v);
  if (feature === "event_rate") return `${fmtNum(v)}/s`;
  return fmtNum(v);
}
