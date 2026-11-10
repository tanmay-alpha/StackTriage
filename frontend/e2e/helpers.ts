import { readFileSync } from "node:fs";
import path from "node:path";
import { expect } from "@playwright/test";
import type { APIRequestContext, Page } from "@playwright/test";

export const FIXTURES = path.resolve(import.meta.dirname, "..", "..", "tests", "fixtures");
export const fixture = (name: string) => path.join(FIXTURES, name);

export interface ImportRow {
  id: number;
  filename: string;
  incident_count: number;
  event_count: number;
}

/** Fail loudly (never skip) when the backend is not reachable. */
export async function requireBackend(request: APIRequestContext, base = ""): Promise<void> {
  const res = await request.get(`${base}/api/health`).catch((e: unknown) => {
    throw new Error(`StackTriage backend is not reachable at ${base || "baseURL"}: ${String(e)}`);
  });
  expect(res.ok(), "backend /api/health must be OK").toBeTruthy();
}

export async function resetImports(request: APIRequestContext, base = "", headers: Record<string, string> = {}): Promise<void> {
  const res = await request.get(`${base}/api/imports?limit=200`, { headers });
  expect(res.ok()).toBeTruthy();
  const body = (await res.json()) as { items: ImportRow[] };
  for (const i of body.items) {
    const del = await request.delete(`${base}/api/imports/${i.id}`, { headers });
    expect(del.status()).toBe(204);
  }
}

export async function seed(request: APIRequestContext, file: string | { name: string; content: Buffer }, opts: { base?: string; headers?: Record<string, string>; service?: string } = {}): Promise<ImportRow & { duplicate: boolean }> {
  const name = typeof file === "string" ? file : file.name;
  const data = typeof file === "string" ? readFileSync(fixture(file)) : file.content;
  const q = new URLSearchParams({ filename: name });
  if (opts.service) q.set("service", opts.service);
  const res = await request.post(`${opts.base ?? ""}/api/imports?${q.toString()}`, { data, headers: { "Content-Type": "application/octet-stream", ...opts.headers } });
  expect(res.ok(), `seeding ${name} failed with ${res.status()}`).toBeTruthy();
  return (await res.json()) as ImportRow & { duplicate: boolean };
}

/** Simulate dropping a file onto the drop zone (Playwright has no native OS drag-and-drop). */
export async function dropFile(page: Page, name: string, content: Buffer | string, type = "text/plain"): Promise<void> {
  const b64 = Buffer.from(content).toString("base64");
  const dt = await page.evaluateHandle(
    ({ b64: data, name: n, type: t }) => {
      const bytes = Uint8Array.from(atob(data), (c) => c.charCodeAt(0));
      const dt = new DataTransfer();
      dt.items.add(new File([bytes], n, { type: t }));
      return dt;
    },
    { b64, name, type },
  );
  const zone = page.getByTestId("dropzone");
  await zone.dispatchEvent("dragenter", { dataTransfer: dt });
  await zone.dispatchEvent("dragover", { dataTransfer: dt });
  await zone.dispatchEvent("drop", { dataTransfer: dt });
}

export async function chooseFile(page: Page, file: string | { name: string; mimeType: string; buffer: Buffer }): Promise<void> {
  const chooser = page.waitForEvent("filechooser");
  await page.getByRole("button", { name: /choose (a different )?file/i }).click();
  const fc = await chooser;
  await fc.setFiles(typeof file === "string" ? fixture(file) : file);
}

export async function setTheme(page: Page, theme: "light" | "dark"): Promise<void> {
  await page.addInitScript((t) => localStorage.setItem("stacktriage.theme", t), theme);
}
