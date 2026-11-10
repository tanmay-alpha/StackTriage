export type LineKind = "header" | "frame" | "library" | "detail" | "libdetail";

export interface StackLine {
  text: string;
  kind: LineKind;
}

const LIB_PATTERNS: RegExp[] = [
  /node_modules/,
  /node:[a-z_/]+/,
  /\(internal\//,
  /site-packages/,
  /[\\/]lib[\\/]python\d/,
  /<frozen /,
  /^\s+at (java|javax|jdk|sun|com\.sun)\./,
  /^\s+at (org\.springframework|org\.apache|org\.hibernate|org\.eclipse|io\.netty|com\.zaxxer|reactor\.core|org\.junit)\./,
];

const AT_FRAME = /^\s+at\s/;
const PY_FRAME = /^\s+File "/;

function isLibrary(line: string): boolean {
  return LIB_PATTERNS.some((re) => re.test(line));
}

/** Classify each trace line. A Python source line under a `File` frame inherits that frame's kind. */
export function classifyStack(trace: string): StackLine[] {
  const out: StackLine[] = [];
  let pyFrame: "frame" | "library" | null = null;
  for (const text of trace.replace(/\r\n?/g, "\n").split("\n")) {
    if (AT_FRAME.test(text)) {
      pyFrame = null;
      out.push({ text, kind: isLibrary(text) ? "library" : "frame" });
    } else if (PY_FRAME.test(text)) {
      pyFrame = isLibrary(text) ? "library" : "frame";
      out.push({ text, kind: pyFrame });
    } else if (pyFrame && /^\s{4,}\S/.test(text)) {
      out.push({ text, kind: pyFrame === "library" ? "libdetail" : "detail" });
    } else {
      pyFrame = null;
      out.push({ text, kind: "header" });
    }
  }
  return out;
}

export function isLibraryKind(k: LineKind): boolean {
  return k === "library" || k === "libdetail";
}
