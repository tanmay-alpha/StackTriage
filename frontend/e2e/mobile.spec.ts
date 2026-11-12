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

test("every card label sits flush left in the stacked incident table", async ({ page, request }) => {
  const imp = await seed(request, "node_express.log");
  await page.goto(`/imports/${imp.id}/incidents`);
  const card = page.locator("[data-testid=incidents-table] tbody tr").first();
  await expect(card).toBeVisible();
  const cells = card.locator("td[data-label]");
  for (let i = 0; i < (await cells.count()); i++) {
    const cell = cells.nth(i);
    const label = await cell.getAttribute("data-label");
    if ((await cell.evaluate((td) => getComputedStyle(td, "::before").display)) === "none") continue;
    // The label is a ::before pseudo-element (not measurable via the DOM), so check pixels:
    // the leftmost 16 CSS px of the cell must contain ink, i.e. the label starts at the left edge.
    const png = (await cell.screenshot()).toString("base64");
    const inked = await page.evaluate(async (b64) => {
      const img = new Image();
      img.src = `data:image/png;base64,${b64}`;
      await img.decode();
      const c = document.createElement("canvas");
      [c.width, c.height] = [img.width, img.height];
      const ctx = c.getContext("2d")!;
      ctx.drawImage(img, 0, 0);
      const strip = Math.round(16 * devicePixelRatio);
      const px = ctx.getImageData(0, 0, strip, img.height).data;
      const [r, g, b] = [px[0], px[1], px[2]];
      for (let p = 0; p < px.length; p += 4) if (Math.abs(px[p] - r) + Math.abs(px[p + 1] - g) + Math.abs(px[p + 2] - b) > 120) return true;
      return false;
    }, png);
    expect(inked, `"${label}" label is not flush left in its card row`).toBe(true);
  }
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

test("contribution tables fit mobile width with wider fallback fonts", async ({ page, request }) => {
  const imp = await seed(request, "spring_boot.log");
  await page.goto(`/?import=${imp.id}`);
  await page.locator(".contrib").first().waitFor();
  // Exercise different platform font metrics instead of relying on the host's system font.
  await page.addStyleTag({ content: ':root { --font-sans: Arial, sans-serif; --font-mono: "Courier New", monospace; }' });
  await noHorizontalOverflow(page);
  for (const table of await page.locator(".contrib").all()) {
    expect(await table.evaluate((el) => el.scrollWidth <= el.clientWidth)).toBe(true);
    expect(await table.evaluate((el) => el.getBoundingClientRect().right <= el.closest(".episode")!.getBoundingClientRect().right)).toBe(true);
  }
});
