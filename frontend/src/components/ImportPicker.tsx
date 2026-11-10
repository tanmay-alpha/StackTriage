import { useId } from "react";
import type { ImportSummary } from "../api/types";
import { useTime } from "../state/app";

export function ImportPicker({ imports, value, onChange, label = "Import" }: { imports: ImportSummary[]; value: number; onChange: (id: number) => void; label?: string }) {
  const id = useId();
  const { fmt } = useTime();
  return (
    <div className="picker">
      <label htmlFor={id} className="picker-label">
        {label}
      </label>
      <select id={id} className="select" value={value} onChange={(e) => onChange(Number(e.target.value))}>
        {imports.map((i) => (
          <option key={i.id} value={i.id}>
            {i.filename} - {fmt.date(i.created_at)} (#{i.id})
          </option>
        ))}
      </select>
    </div>
  );
}
