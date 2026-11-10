import { expect, test } from "@playwright/test";
import { API_KEY, AUTH_PORT } from "../playwright.config";
import { chooseFile, requireBackend, resetImports, seed } from "./helpers";

const BASE = `http://127.0.0.1:${AUTH_PORT}`;
const authHeader = { Authorization: `Bearer ${API_KEY}` };

test.use({ baseURL: BASE });

test.beforeEach(async ({ request }) => {
  await requireBackend(request, BASE);
  await resetImports(request, BASE, authHeader);
});

test("(e) API key gate: dialog, wrong key, right key, session-only storage, forget", async ({ page, request }) => {
  // the server really enforces the key
  expect((await request.get("/api/imports")).status()).toBe(401);
  expect((await request.get("/api/imports", { headers: { Authorization: "Bearer nope-nope-nope-nope" } })).status()).toBe(401);
  await seed(request, "spring_boot.log", { headers: authHeader });

  const seenUrls: string[] = [];
  page.on("request", (r) => seenUrls.push(r.url()));

  await page.goto("/");
  const dialog = page.getByRole("dialog", { name: "API key required" });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByLabel("API key")).toBeFocused();

  // wrong key
  await dialog.getByLabel("API key").fill("definitely-the-wrong-key-123");
  await dialog.getByRole("button", { name: "Unlock" }).click();
  await expect(dialog.getByRole("alert")).toContainText("not accepted");
  await expect(dialog).toBeVisible();
  expect(await page.evaluate(() => sessionStorage.getItem("stacktriage.apiKey"))).toBeNull();

  // right key
  await dialog.getByLabel("API key").fill(API_KEY);
  await dialog.getByRole("button", { name: "Unlock" }).click();
  await expect(dialog).toBeHidden();
  await expect(page.getByRole("heading", { name: "Log volume over time" })).toBeVisible();
  await expect(page.getByText("spring_boot.log").first()).toBeVisible();

  // key lives in sessionStorage only and never in a URL
  expect(await page.evaluate(() => sessionStorage.getItem("stacktriage.apiKey"))).toBe(API_KEY);
  expect(await page.evaluate(() => Object.keys(localStorage).some((k) => localStorage.getItem(k)?.includes("e2e-secret")))).toBe(false);
  expect(seenUrls.some((u) => u.includes(API_KEY))).toBe(false);
  expect(page.url()).not.toContain(API_KEY);

  // survives reload within the session, no dialog
  await page.reload();
  await expect(page.getByRole("heading", { name: "Log volume over time" })).toBeVisible();
  await expect(dialog).toBeHidden();

  // authenticated upload (XHR) and export download (fetch -> Blob) both carry the header
  await page.goto("/import");
  await chooseFile(page, "node_express.log");
  await page.getByRole("button", { name: "Upload and analyse" }).click();
  await expect(page.getByRole("region", { name: "Ingestion summary" })).toBeVisible();
  await page.getByRole("link", { name: /View \d+ incidents?/ }).click();
  const dl = page.waitForEvent("download");
  await page.getByRole("button", { name: "Export report as JSON" }).click();
  expect((await dl).suggestedFilename()).toMatch(/\.json$/);

  // forgetting the key brings the gate back
  await page.getByRole("button", { name: "API key", exact: false }).click();
  await expect(dialog).toBeVisible();
  await dialog.getByRole("button", { name: "Forget stored key" }).click();
  expect(await page.evaluate(() => sessionStorage.getItem("stacktriage.apiKey"))).toBeNull();
  await expect(dialog).toBeVisible();
  await dialog.getByRole("button", { name: "Cancel" }).click();
  await expect(dialog).toBeHidden();
  // views now explain the problem and offer a retry path
  await page.goto("/");
  await expect(dialog).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();
  await expect(page.getByRole("alert").filter({ hasText: /API key/ })).toBeVisible();
});

test("a rejected key mid-session reopens the gate", async ({ page }) => {
  await page.goto("/");
  await page.evaluate(() => sessionStorage.setItem("stacktriage.apiKey", "stale-key-that-is-wrong-0000"));
  await page.reload();
  await expect(page.getByRole("dialog", { name: "API key required" })).toBeVisible();
});
