import { mkdirSync } from "node:fs";
import path from "node:path";
import { expect, test } from "@playwright/test";
import { chooseFile, requireBackend, resetImports, seed, setTheme } from "./helpers";

const OUT = path.resolve(import.meta.dirname, "..", "..", "docs", "screenshots");
mkdirSync(OUT, { recursive: true });
const shot = (name: string) => path.join(OUT, `${name}.png`);

const clearToasts = (page: import("@playwright/test").Page) => page.evaluate(() => document.querySelectorAll<HTMLButtonElement>(".toast-x").forEach((b) => b.click()));

test.describe.configure({ mode: "serial" });

interface Seeded {
  spring: number;
  telemetry: number;
}
let ids: Seeded;

test.beforeAll(async ({ request }) => {
  await requireBackend(request);
  await resetImports(request);
  const spring = await seed(request, "spring_boot.log");
  const telemetry = await seed(request, "telemetry.csv");
  ids = { spring: spring.id, telemetry: telemetry.id };
});

test("overview, import result, incidents, investigation, dark overview (desktop 1440x900)", async ({ page, request }) => {
  // Overview of the Spring Boot import, with a chart tooltip on an unusual window
  await page.goto(`/?import=${ids.spring}`);
  await expect(page.locator("svg.chart-svg rect.chart-bar").first()).toBeVisible();
  const svg = page.locator(".chart-svg").first();
  const box = (await svg.boundingBox())!;
  await page.mouse.move(box.x + box.width * 0.62, box.y + box.height * 0.45);
  await expect(page.locator(".chart-tip").first()).toBeVisible();
  await page.screenshot({ path: shot("overview") });

  // Import: upload the Node/Express fixture through the UI and show the ingestion summary
  await page.goto("/import");
  await chooseFile(page, "node_express.log");
  await page.getByRole("button", { name: "Upload and analyse" }).click();
  const summary = page.getByRole("region", { name: "Ingestion summary" });
  await expect(summary).toBeVisible();
  const imports = (await (await request.get("/api/imports?limit=10")).json()) as { items: { id: number; filename: string }[] };
  const node = imports.items.find((i) => i.filename === "node_express.log")!;
  await summary.getByRole("tab", { name: /Redactions/ }).click();
  await page.evaluate(() => window.scrollTo(0, 0));
  await summary.scrollIntoViewIfNeeded();
  await page.evaluate(() => {
    const el = document.querySelector(".result-panel");
    if (el) window.scrollTo(0, (el as HTMLElement).getBoundingClientRect().top + window.scrollY - 64);
  });
  await clearToasts(page);
  await page.screenshot({ path: shot("import-result") });

  // Triage state so the incident list shows more than "open"
  const list = (await (await request.get(`/api/imports/${node.id}/incidents?limit=10`)).json()) as { items: { fingerprint: string; status: string; title: string }[] };
  const [a, b] = list.items;
  if (a) await request.put(`/api/incidents/${a.fingerprint}/status`, { data: { status: "investigating" } });
  if (b) await request.put(`/api/incidents/${b.fingerprint}/status`, { data: { status: "resolved" } });
  const te = (list.items as { fingerprint: string; title: string }[]).find((i) => i.title.startsWith("TypeError"));
  if (te) await request.post(`/api/incidents/${te.fingerprint}/notes`, { data: { body: "Session object is undefined after token refresh. Added a null guard in the auth middleware; verifying on staging before closing." } });

  await page.goto(`/imports/${node.id}/incidents`);
  await expect(page.locator("[data-testid=incidents-table] tbody tr").first()).toBeVisible();
  await page.screenshot({ path: shot("incidents") });

  // Investigation of the most frequent incident
  await page.locator(".title-link", { hasText: "TypeError" }).first().click();
  await expect(page.getByLabel("Stack trace", { exact: true }).first()).toBeVisible();
  await expect(page.getByRole("list", { name: "Notes" })).toBeVisible();
  await page.getByRole("checkbox", { name: /import-wide errors/ }).check();
  await page.mouse.move(0, 0);
  const inv = (await page.locator(".chart-svg").first().boundingBox())!;
  await page.mouse.move(inv.x + inv.width * 0.5, inv.y + inv.height * 0.5);
  await page.evaluate(() => {
    const panel = document.querySelector(".investigation .panel");
    if (panel) window.scrollTo(0, (panel as HTMLElement).getBoundingClientRect().top + window.scrollY - 70);
  });
  await clearToasts(page);
  await page.mouse.move(inv.x + inv.width * 0.5, 160);
  await page.screenshot({ path: shot("investigation") });

  // Dark overview of the telemetry import (metrics chart + sustained shift)
  const dark = await page.context().browser()!.newContext({ viewport: { width: 1440, height: 900 }, colorScheme: "dark", baseURL: "http://127.0.0.1:8765" });
  const dp = await dark.newPage();
  await setTheme(dp, "dark");
  await dp.goto(`/?import=${ids.telemetry}`);
  await expect(dp.locator("svg.chart-svg rect.chart-bar").first()).toBeVisible();
  await expect(dp.getByRole("heading", { name: "Unusual periods" })).toBeVisible();
  await dp.screenshot({ path: shot("dark-overview") });
  await dark.close();
});

test("mobile incidents (390x844)", async ({ browser, request }) => {
  const list = (await (await request.get("/api/imports?limit=10")).json()) as { items: { id: number; filename: string }[] };
  const node = list.items.find((i) => i.filename === "node_express.log")!;
  const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true, baseURL: "http://127.0.0.1:8765" });
  const page = await ctx.newPage();
  await page.goto(`/imports/${node.id}/incidents`);
  await expect(page.locator("[data-testid=incidents-table] tbody tr").first()).toBeVisible();
  await page.screenshot({ path: shot("mobile-incidents") });
  await ctx.close();
});

test("compare: desktop 1440x900 and mobile 390x844", async ({ page, browser, request }) => {
  const before = await seed(request, "deploy_before.log");
  const after = await seed(request, "deploy_after.log");
  // one group marked resolved earlier, so the "Marked resolved, observed in target" flag is visible
  const res = await request.get(`/api/imports/${before.id}/compare/${after.id}?limit=200`);
  const body: { items: { fingerprint: string; title: string }[] } = await res.json();
  const inventory = body.items.find((i) => i.title.includes("warehouse API refused connection"));
  if (inventory) await request.put(`/api/incidents/${inventory.fingerprint}/status`, { data: { status: "resolved" } });
  const url = `/compare?baseline=${before.id}&target=${after.id}`;

  await page.goto(url);
  await expect(page.locator("[data-testid=compare-table] tbody tr").first()).toBeVisible();
  await page.screenshot({ path: shot("compare") });

  const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true, baseURL: "http://127.0.0.1:8765" });
  const mobile = await ctx.newPage();
  await mobile.goto(url);
  await expect(mobile.locator("[data-testid=compare-table] tbody tr").first()).toBeVisible();
  // the capture summary is in the desktop shot; show the findings and the first result cards here
  await mobile.evaluate(() => {
    const chips = document.querySelector(".cmp-chips");
    if (chips) window.scrollTo(0, chips.getBoundingClientRect().top + window.scrollY - 16);
  });
  await mobile.screenshot({ path: shot("compare-mobile") });
  await ctx.close();
});
