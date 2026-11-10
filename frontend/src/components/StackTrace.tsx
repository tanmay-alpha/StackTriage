import { useMemo, useState } from "react";
import type { ReactNode } from "react";
import { classifyStack, isLibraryKind } from "../lib/stack";
import type { StackLine } from "../lib/stack";
import { useToast } from "../state/app";
import { Button } from "./ui";

export async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.setAttribute("readonly", "");
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    let ok = false;
    try {
      ok = document.execCommand("copy");
    } catch {
      ok = false;
    }
    ta.remove();
    return ok;
  }
}

export function CopyButton({ text, label, children }: { text: string; label: string; children?: ReactNode }) {
  const { push } = useToast();
  const [done, setDone] = useState(false);
  return (
    <Button
      size="sm"
      variant="ghost"
      aria-label={label}
      onClick={async () => {
        const ok = await copyText(text);
        if (ok) {
          setDone(true);
          push("Copied to clipboard.");
          window.setTimeout(() => setDone(false), 1800);
        } else push("Could not copy. Select the text and copy it manually.", "error");
      }}
    >
      {done ? "Copied" : (children ?? "Copy")}
    </Button>
  );
}

interface Segment {
  kind: "line" | "hidden";
  lines: StackLine[];
  start: number;
}

export function StackTrace({ trace, label = "Stack trace" }: { trace: string; label?: string }) {
  const [showAll, setShowAll] = useState(false);
  const lines = useMemo(() => classifyStack(trace), [trace]);
  const hiddenCount = useMemo(() => lines.filter((l) => l.kind === "library").length, [lines]);

  const segments = useMemo(() => {
    const out: Segment[] = [];
    lines.forEach((l, i) => {
      const hide = !showAll && isLibraryKind(l.kind);
      const last = out[out.length - 1];
      if (hide) {
        if (last && last.kind === "hidden") last.lines.push(l);
        else out.push({ kind: "hidden", lines: [l], start: i });
      } else out.push({ kind: "line", lines: [l], start: i });
    });
    return out;
  }, [lines, showAll]);

  return (
    <div className="trace">
      <div className="trace-bar">
        <span className="trace-label">{label}</span>
        <span className="trace-tools">
          {hiddenCount > 0 ? (
            <Button size="sm" variant="ghost" aria-pressed={showAll} onClick={() => setShowAll((s) => !s)}>
              {showAll ? "Collapse library frames" : `Show all frames (${hiddenCount} library hidden)`}
            </Button>
          ) : null}
          <CopyButton text={trace} label={`Copy ${label.toLowerCase()}`} />
        </span>
      </div>
      <pre className="trace-body" tabIndex={0} aria-label={label}>
        <code>
          {segments.map((seg) =>
            seg.kind === "line" ? (
              <span key={seg.start} className={`tl tl-${seg.lines[0]!.kind}`} data-n={seg.start + 1}>
                {seg.lines[0]!.text}
                {"\n"}
              </span>
            ) : (
              <span key={seg.start} className="tl tl-hidden" data-n="">
                <button type="button" className="tl-more" onClick={() => setShowAll(true)}>
                  {`... ${seg.lines.filter((l) => l.kind === "library").length} library frame${seg.lines.filter((l) => l.kind === "library").length === 1 ? "" : "s"} hidden`}
                </button>
                {"\n"}
              </span>
            ),
          )}
        </code>
      </pre>
    </div>
  );
}
