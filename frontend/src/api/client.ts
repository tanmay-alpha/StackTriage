import type { CompareBasis, ImportSummary } from "./types";

const KEY_STORAGE = "stacktriage.apiKey";

export class ApiError extends Error {
  readonly status: number;
  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

/* ---- API key: sessionStorage (or page memory if blocked), never in URLs ---- */
type Listener = () => void;
const listeners = new Set<Listener>();
let memoryKey: string | null | undefined;

export function getApiKey(): string | null {
  if (memoryKey !== undefined) return memoryKey;
  try {
    return sessionStorage.getItem(KEY_STORAGE);
  } catch {
    return null;
  }
}

export function setApiKey(key: string | null): void {
  try {
    if (key) sessionStorage.setItem(KEY_STORAGE, key);
    else sessionStorage.removeItem(KEY_STORAGE);
    memoryKey = undefined;
  } catch {
    memoryKey = key;
  }
  listeners.forEach((l) => l());
}

export function onAuthChange(l: Listener): () => void {
  listeners.add(l);
  return () => listeners.delete(l);
}

const unauthorizedListeners = new Set<Listener>();
export function onUnauthorized(l: Listener): () => void {
  unauthorizedListeners.add(l);
  return () => unauthorizedListeners.delete(l);
}

function authHeaders(): Record<string, string> {
  const key = getApiKey();
  return key ? { Authorization: `Bearer ${key}` } : {};
}

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null;
}

export function describeError(status: number, body: unknown, fallback: string): string {
  if (isRecord(body) && "detail" in body) {
    const d = body.detail;
    if (typeof d === "string") return d;
    if (Array.isArray(d)) {
      const parts: string[] = [];
      for (const e of d) {
        if (!isRecord(e) || typeof e.msg !== "string") continue;
        const loc = Array.isArray(e.loc) ? e.loc.filter((p) => p !== "query" && p !== "body").join(".") : "";
        parts.push(loc ? `${loc}: ${e.msg}` : e.msg);
      }
      if (parts.length) return parts.join("; ");
    }
  }
  if (status === 0) return "Cannot reach the StackTriage server.";
  return fallback;
}

async function parseBody(res: Response): Promise<unknown> {
  const text = await res.text();
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

type Params = Record<string, string | number | boolean | null | undefined>;

function qs(params?: Params): string {
  if (!params) return "";
  const u = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null || v === "") continue;
    u.set(k, String(v));
  }
  const s = u.toString();
  return s ? `?${s}` : "";
}

export async function request<T>(method: string, path: string, opts: { params?: Params; body?: unknown; signal?: AbortSignal } = {}): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`/api${path}${qs(opts.params)}`, {
      method,
      signal: opts.signal,
      headers: {
        Accept: "application/json",
        ...(opts.body !== undefined ? { "Content-Type": "application/json" } : {}),
        ...authHeaders(),
      },
      body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
    });
  } catch (e) {
    if (e instanceof DOMException && e.name === "AbortError") throw e;
    throw new ApiError(0, "Cannot reach the StackTriage server.");
  }
  const body = res.status === 204 ? null : await parseBody(res);
  if (!res.ok) {
    if (res.status === 401) unauthorizedListeners.forEach((l) => l());
    throw new ApiError(res.status, describeError(res.status, body, `Request failed (${res.status})`));
  }
  return body as T;
}

export const api = {
  get: <T>(path: string, params?: Params, signal?: AbortSignal) => request<T>("GET", path, { params, signal }),
  put: <T>(path: string, body: unknown) => request<T>("PUT", path, { body }),
  post: <T>(path: string, body: unknown) => request<T>("POST", path, { body }),
  del: (path: string) => request<null>("DELETE", path),
};

export async function fetchHealth(): Promise<{ status: string; auth_required: boolean }> {
  const res = await fetch("/api/health", { headers: { Accept: "application/json" } });
  if (!res.ok) throw new ApiError(res.status, "Health check failed");
  return (await res.json()) as { status: string; auth_required: boolean };
}

/** Verify a candidate key without storing it. */
export async function verifyKey(key: string): Promise<boolean> {
  const res = await fetch("/api/overview", { headers: { Authorization: `Bearer ${key}` } });
  if (res.status === 401) return false;
  if (!res.ok) throw new ApiError(res.status, `Request failed (${res.status})`);
  return true;
}

/* ---- upload with progress (XHR) ---- */
export interface UploadHandlers {
  onProgress: (loaded: number, total: number) => void;
  onUploaded: () => void;
}

export interface UploadResult {
  summary: ImportSummary;
  duplicate: boolean;
}

export function uploadLog(
  file: File,
  opts: { service?: string; format?: string },
  h: UploadHandlers,
): { promise: Promise<UploadResult>; abort: () => void } {
  const xhr = new XMLHttpRequest();
  const { promise, resolve, reject } = Promise.withResolvers<UploadResult>();
  {
    xhr.open("POST", `/api/imports${qs({ filename: file.name, service: opts.service, format: opts.format })}`);
    const key = getApiKey();
    if (key) xhr.setRequestHeader("Authorization", `Bearer ${key}`);
    xhr.setRequestHeader("Content-Type", "application/octet-stream");
    xhr.setRequestHeader("Accept", "application/json");
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) h.onProgress(e.loaded, e.total);
    };
    xhr.upload.onload = () => h.onUploaded();
    xhr.onerror = () => reject(new ApiError(0, "Cannot reach the StackTriage server. The upload did not complete."));
    xhr.onabort = () => reject(new ApiError(0, "Upload cancelled."));
    xhr.ontimeout = () => reject(new ApiError(0, "The upload timed out."));
    xhr.onload = () => {
      let body: unknown = null;
      try {
        body = xhr.responseText ? JSON.parse(xhr.responseText) : null;
      } catch {
        body = null;
      }
      if (xhr.status === 200 || xhr.status === 201) {
        const summary = body as ImportSummary;
        resolve({ summary, duplicate: xhr.status === 200 && summary.duplicate === true });
        return;
      }
      if (xhr.status === 401) unauthorizedListeners.forEach((l) => l());
      reject(new ApiError(xhr.status, describeError(xhr.status, body, `Upload failed (${xhr.status})`)));
    };
    xhr.send(file);
  }
  return { promise, abort: () => xhr.abort() };
}

/* ---- authenticated download (fetch -> Blob) ---- */
export type ExportFormat = "csv" | "json" | "md";

async function download(path: string, fallbackName: string): Promise<string> {
  let res: Response;
  try {
    res = await fetch(`/api${path}`, { headers: authHeaders() });
  } catch {
    throw new ApiError(0, "Cannot reach the StackTriage server.");
  }
  if (!res.ok) {
    if (res.status === 401) unauthorizedListeners.forEach((l) => l());
    throw new ApiError(res.status, describeError(res.status, await parseBody(res), `Export failed (${res.status})`));
  }
  const blob = await res.blob();
  const cd = res.headers.get("Content-Disposition") ?? "";
  const m = /filename="?([^";]+)"?/i.exec(cd);
  const name = m?.[1] ?? fallbackName;
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
  return name;
}

export function downloadExport(importId: number, format: ExportFormat): Promise<string> {
  return download(`/imports/${importId}/export${qs({ format })}`, `stacktriage-${importId}.${format}`);
}

export function downloadComparison(baselineId: number, targetId: number, format: ExportFormat, basis: CompareBasis): Promise<string> {
  return download(`/imports/${baselineId}/compare/${targetId}/export${qs({ format, basis })}`, `stacktriage-compare-${baselineId}-vs-${targetId}.${format}`);
}
