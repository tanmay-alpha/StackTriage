import { expect, test } from "@playwright/test";
import { requireBackend, resetImports, seed } from "./helpers";

test.use({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true });

test.beforeEach(async ({ request }) => {
  await requireBackend(request);
  await resetImports(request);
});

async function noHorizontalOverflow(page: import("@playwright/test").Page) {
  const { sw, iw } = await page.evaluate(() => ({ sw: document.documentElement.scrollWidth, iw: document.documentElement.clientWidth }));
  expect(sw, `page must not scroll horizontally (scrollWidth ${sw} > ${iw})`).toBeLessThanOrEqual(iw);
}

test("(g) all four views work at 390x844 without horizontal scrolling", async ({ page, request }) => {
  const imp = await seed(request, "spring_boot.log");
  await seed(request, "telemetry.csv");

  await page.goto(`/?import=${imp.id}`);
  await expect(page.getByRole("heading", { level: 1, name: "Overview" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Log volume over time" })).toBeVisible();
  await expect(page.locator("svg.chart-svg").first()).toBeVisible();
  await noHorizontalOverflow(page);

  // navigation collapses behind a menu button
  const menu = page.getByRole("button", { name: "Menu" });
  await expect(menu).toBeVisible();
  await expect(page.getByRole("link", { name: "Import", exact: true })).toBeHidden();
  await menu.click();
  await expect(menu).toHaveAttribute("aria-expanded", "true");
  await page.getByRole("link", { name: "Import", exact: true }).click();

  await expect(page.getByRole("heading", { level: 1, name: "Import a log" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Choose file" })).toBeVisible();
  await expect(page.getByTestId("imports-table")).toBeVisible();
  await noHorizontalOverflow(page);

  await page.goto(`/imports/${imp.id}/incidents`);
  await expect(page.getByRole("heading", { level: 1, name: "Incidents" })).toBeVisible();
  const rows = page.locator("[data-testid=incidents-table] tbody tr");
  await expect(rows).toHaveCount(4);
  // table collapsed into cards: header row is visually removed, every card is labelled
  expect((await rows.first().boundingBox())!.width).toBeGreaterThan(300);
  await expect(rows.first().locator("td[data-label=Count]")).toBeVisible();
  await noHorizontalOverflow(page);

  await page.locator(".title-link").first().click();
  await expect(page.getByRole("heading", { level: 1 })).toContainText("SQLTransientConnectionException");
  await expect(page.getByLabel("Stack trace", { exact: true }).first()).toBeVisible();
  await expect(page.getByRole("heading", { name: /Investigation notes/ })).toBeVisible();
  await noHorizontalOverflow(page);
  // the stack trace scrolls inside its own box instead of widening the page
  const trace = page.locator(".trace-body").first();
  expect(await trace.evaluate((el) => el.scrollWidth > el.clientWidth)).toBe(true);
});

test("long service names, filenames and messages remain readable on mobile", async ({ page, request }) => {
  const service = `checkout-${"x".repeat(80)}`;
  const message = `UpstreamFailure: ${"long-message-".repeat(80)}`;
  const stack = `Error: ${message}\n    at checkout (/srv/${"path/".repeat(80)}handler.js:12:3)`;
  const imp = await seed(request, {
    name: `${"long-filename-".repeat(12)}.jsonl`,
    content: Buffer.from(JSON.stringify({ timestamp: "2026-03-05T10:00:00Z", level: "error", service, message, stack }) + "\n"),
  });
  await page.goto(`/?import=${imp.id}`);
  await expect(page.getByRole("heading", { name: "Events by level and service" })).toBeVisible();
  await noHorizontalOverflow(page);
  await expect(page.locator(".kv-list").getByText(service, { exact: true })).toBeVisible();
  await page.goto("/import");
  await expect(page.getByTestId("imports-table")).toBeVisible();
  await noHorizontalOverflow(page);
  await page.goto(`/imports/${imp.id}/incidents`);
  await expect(page.locator(".title-link")).toBeVisible();
  await noHorizontalOverflow(page);
  await page.locator(".title-link").click();
  await expect(page.locator(".message-block")).toHaveText(message);
  await expect(page.locator(".trace-body")).toContainText("handler.js:12:3");
  await noHorizontalOverflow(page);
});
