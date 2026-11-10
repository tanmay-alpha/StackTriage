export type Status = "open" | "investigating" | "resolved" | "ignored";
export const STATUSES: Status[] = ["open", "investigating", "resolved", "ignored"];

export interface Page<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

export interface ImportSummary {
  id: number;
  filename: string;
  format: string;
  default_service: string | null;
  size_bytes: number;
  created_at: string;
  event_count: number;
  incident_count: number;
  first_event: string | null;
  last_event: string | null;
  window_seconds: number;
  truncated: boolean;
  duplicate: boolean;
}

export interface TimelineBucket {
  start_ms: number;
  total: number;
  debug: number;
  info: number;
  warning: number;
  error: number;
  critical: number;
  unknown: number;
  distinct_groups: number;
  new_groups: number;
  metrics?: Record<string, number>;
}

export interface DiagnosticIssue {
  code: string;
  line: number | null;
  message: string;
}

export interface Diagnostics {
  lines_read?: number;
  bytes_read?: number;
  events_parsed?: number;
  events_without_timestamp?: number;
  truncated?: boolean;
  truncated_reason?: string | null;
  code_counts?: Record<string, number>;
  issues?: DiagnosticIssue[];
  redactions?: Record<string, number>;
}

export interface Contribution {
  feature: string;
  observed: number;
  baseline: number;
  deviation: number;
}

export interface AnomalyWindow {
  start: string;
  end: string;
  score: number;
  is_anomalous: boolean;
  classification: string;
  severity: string | null;
  features?: Record<string, number>;
  contributions?: Contribution[];
  used_for_training?: boolean;
}

export interface AnomalyEpisode {
  start: string;
  end: string;
  peak_score: number;
  classification: string;
  severity: string | null;
  window_count: number;
  evidence?: string[];
  features?: string[];
}

export interface AnomalyReport {
  method_used?: string;
  requested_method?: string;
  windows?: AnomalyWindow[];
  train_range?: [string, string] | null;
  episodes?: AnomalyEpisode[];
  notes?: string[];
  error?: string;
}

export interface ImportDetail extends ImportSummary {
  level_counts: Record<string, number>;
  service_counts: Record<string, number>;
  diagnostics: Diagnostics;
  timeline: TimelineBucket[];
  anomaly: AnomalyReport | null;
  notes: string[];
}

export interface Activity {
  state: "ongoing" | "ended" | "unknown";
  recurrence: string;
  period_seconds: number | null;
  rate_per_minute: number | null;
  active_windows: number;
  peak_window_start: string | null;
  peak_window_count: number;
}

export interface IncidentItem {
  fingerprint: string;
  title: string;
  service: string;
  exc_type: string | null;
  frame: string | null;
  max_level: string;
  count: number;
  first_seen: string | null;
  last_seen: string | null;
  status: Status;
  activity: Activity;
}

export interface NoteOut {
  id: number;
  body: string;
  created_at: string;
}

export interface WindowCount {
  start: string;
  count: number;
}

export interface IncidentDetail extends IncidentItem {
  kind: string;
  untimed_count: number;
  sample_event_id: string;
  sample_message: string;
  sample_stack_trace: string | null;
  level_counts: Record<string, number>;
  window_seconds: number;
  windows: WindowCount[];
  notes: NoteOut[];
  related: IncidentItem[];
}

export interface EventOut {
  event_id: string;
  line_no: number;
  timestamp: string | null;
  level: string;
  service: string | null;
  logger: string | null;
  message: string;
  stack_trace: string | null;
  fields: Record<string, string>;
  metrics: Record<string, number>;
  fingerprint: string | null;
}

export interface Overview {
  imports: number;
  events: number;
  groups: number;
  latest_import: ImportSummary | null;
  status_counts: Record<string, number>;
}

export const LOG_FORMATS = ["python", "node", "java", "jsonl", "csv", "text"] as const;
