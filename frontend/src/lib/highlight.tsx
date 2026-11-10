import type { ReactNode } from "react";

/** Split `text` on case-insensitive occurrences of `q`; matches become <mark>. Text nodes only. */
export function highlight(text: string, q: string | undefined | null): ReactNode {
  const needle = (q ?? "").trim();
  if (!needle) return text;
  const lower = text.toLowerCase();
  const n = needle.toLowerCase();
  const out: ReactNode[] = [];
  let i = 0;
  let k = 0;
  while (i < text.length) {
    const j = lower.indexOf(n, i);
    if (j === -1) {
      out.push(text.slice(i));
      break;
    }
    if (j > i) out.push(text.slice(i, j));
    out.push(<mark key={k++}>{text.slice(j, j + n.length)}</mark>);
    i = j + n.length;
  }
  return out;
}
