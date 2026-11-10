import { expect, test } from "@playwright/test";
import { chooseFile, requireBackend, resetImports } from "./helpers";

const PAYLOAD = "<img src=x onerror=window.__xss=1>";

test.beforeEach(async ({ request }) => {
  await requireBackend(request);
  await resetImports(request);
});

test("(d) hostile log content is rendered as text and never executes", async ({ page }) => {
  const dialogs: string[] = [];
  page.on("dialog", async (d) => {
    dialogs.push(d.message());
    await d.dismiss();
  });
  const lines = [
    { timestamp: "2026-03-05T10:00:00Z", level: "info", service: "web", message: "startup complete" },
    {
      timestamp: "2026-03-05T10:01:00Z",
      level: "error",
      service: "<svg onload=window.__xss=2>",
      message: PAYLOAD,
      logger: "<script>window.__xss=3</script>",
      user: "<b onmouseover=window.__xss=4>bob</b>",
      stack: `Error: ${PAYLOAD}\n    at handler (/srv/app/<script>window.__xss=5</script>.js:10:5)\n    at Layer.handle (/srv/app/node_modules/express/lib/router/layer.js:95:5)`,
    },
    { timestamp: "2026-03-05T10:02:00Z", level: "error", service: "<svg onload=window.__xss=2>", message: PAYLOAD, user: "<b onmouseover=window.__xss=4>bob</b>" },
  ]
    .map((o) => JSON.stringify(o))
    .join("\n");

  await page.goto("/import");
  await chooseFile(page, { name: "xss-test.jsonl", mimeType: "application/json", buffer: Buffer.from(lines + "\n") });
  await page.getByRole("button", { name: "Upload and analyse" }).click();
  const summary = page.getByRole("region", { name: "Ingestion summary" });
  await expect(summary).toBeVisible();
  await summary.getByRole("link", { name: /View \d+ incidents?/ }).click();

  // incidents table: title is literal text, highlighting keeps it text
  await expect(page.getByRole("heading", { level: 1, name: "Incidents" })).toBeVisible();
  const title = page.locator(".title-link").first();
  // group titles are normalised by the server (numbers become <N>) but remain literal text
  await expect(title).toContainText("<img src=x onerror=window.__xss=");
  await page.getByLabel("Search").fill("onerror");
  await expect(page.locator("mark").first()).toBeVisible();
  await expect(title).toContainText("<img src=x onerror=window.__xss=");

  // investigation: message, stack trace, records, fields, copy
  await title.click();
  await expect(page.locator(".message-block")).toHaveText(PAYLOAD);
  await expect(page.getByLabel("Stack trace", { exact: true }).first()).toContainText("<script>window.__xss=5</script>");
  await expect(page.locator(".event-msg").first()).toHaveText(PAYLOAD);
  await page.locator(".event").first().getByRole("button", { name: "Details" }).click();
  await expect(page.locator(".event-detail")).toContainText("<b onmouseover=window.__xss=4>bob</b>");

  // overview "top error groups"
  await page.goto("/");
  await expect(page.locator(".top-title").first()).toContainText("<img src=x onerror=window.__xss=");

  // notes are text too
  await page.goBack();
  await page.getByLabel("Add a note").fill("<img src=x onerror=window.__xss=6>");
  await page.getByRole("button", { name: "Add note" }).click();
  await expect(page.getByRole("list", { name: "Notes" })).toContainText("<img src=x onerror=window.__xss=6>");

  // nothing executed and no injected elements exist anywhere
  expect(await page.evaluate(() => (window as unknown as Record<string, unknown>).__xss)).toBeUndefined();
  expect(await page.locator("img[src=x], svg[onload], script:not([src]), b[onmouseover]").count()).toBe(0);
  expect(dialogs).toEqual([]);
});

test("the app sends no requests to other origins", async ({ page, request, baseURL }) => {
  const external: string[] = [];
  page.on("request", (r) => {
    const u = new URL(r.url());
    if (u.origin !== new URL(baseURL!).origin && u.protocol.startsWith("http")) external.push(r.url());
  });
  const res = await request.get("/");
  expect(res.headers()["content-security-policy"]).toContain("default-src 'self'");
  for (const path of ["/", "/import", "/incidents"]) {
    await page.goto(path);
    await page.waitForLoadState("networkidle");
  }
  expect(external).toEqual([]);
});
