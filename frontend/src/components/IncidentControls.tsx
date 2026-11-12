import { useState } from "react";
import { api, downloadExport } from "../api/client";
import type { ExportFormat } from "../api/client";
import type { Status } from "../api/types";
import { STATUSES } from "../api/types";
import { useToast } from "../state/app";
import { Button, STATUS_LABEL } from "./ui";

export function StatusSelect({ fingerprint, value, label, onChanged, size = "sm" }: { fingerprint: string; value: Status; label: string; onChanged: (s: Status) => void; size?: "sm" | "md" }) {
  const { push } = useToast();
  const [busy, setBusy] = useState(false);
  const change = async (next: Status) => {
    if (next === value) return;
    const prev = value;
    onChanged(next);
    setBusy(true);
    try {
      await api.put(`/incidents/${fingerprint}/status`, { status: next });
      push(`Status set to ${STATUS_LABEL[next].toLowerCase()}.`);
    } catch (e) {
      onChanged(prev);
      push(e instanceof Error ? `Could not change status: ${e.message}` : "Could not change status.", "error");
    } finally {
      setBusy(false);
    }
  };
  return (
    <select className={`select status-select st-${value} select-${size}`} value={value} aria-label={`Status: ${label}`} aria-busy={busy || undefined} onChange={(e) => void change(e.target.value as Status)}>
      {STATUSES.map((s) => (
        <option key={s} value={s}>
          {STATUS_LABEL[s]}
        </option>
      ))}
    </select>
  );
}

const FORMATS: { id: ExportFormat; label: string }[] = [
  { id: "csv", label: "CSV" },
  { id: "md", label: "Markdown" },
  { id: "json", label: "JSON" },
];

export function ExportButtons({ importId }: { importId: number }) {
  return <DownloadButtons groupLabel="Export incident report for this import" noun="report" download={(f) => downloadExport(importId, f)} />;
}

export function DownloadButtons({ groupLabel, noun, download }: { groupLabel: string; noun: string; download: (format: ExportFormat) => Promise<string> }) {
  const { push } = useToast();
  const [busy, setBusy] = useState<string | null>(null);
  const go = async (f: ExportFormat) => {
    setBusy(f);
    try {
      const name = await download(f);
      push(`Downloaded ${name}.`);
    } catch (e) {
      push(e instanceof Error ? `Export failed: ${e.message}` : "Export failed.", "error");
    } finally {
      setBusy(null);
    }
  };
  return (
    <div className="btn-group" role="group" aria-label={groupLabel}>
      <span className="btn-group-label">Export</span>
      {FORMATS.map((f) => (
        <Button key={f.id} size="sm" busy={busy === f.id} onClick={() => void go(f.id)} aria-label={`Export ${noun} as ${f.label}`}>
          {f.label}
        </Button>
      ))}
    </div>
  );
}
