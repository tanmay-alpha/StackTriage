import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";
import { requireBackend, resetImports, seed } from "./helpers";

// Every row of a stat strip must reach the strip's right edge; otherwise the 1px separator
// background shows through as an empty grey cell.
async function statStripsFilled(page: Page, where: string) {
  const gaps = await page.locator(".stat-strip").evaluateAll((strips) =>
    strips.flatMap((strip) => {
      const inner = strip.getBoundingClientRect().right - strip.clientLeft;
      const rows = new Map<number, number>();
      for (const item of Array.from(strip.children)) {
        const r = item.getBoundingClientRect();
        const top = Math.round(r.top);
        rows.set(top, Math.max(rows.get(top) ?? 0, r.right));
      }
      return [...rows.values()].filter((right) => inner - right > 2).map((right) => Math.round(inner - right));
    }),
  );
  expect(gaps, `${where}: stat strip rows leave empty space (px)`).toEqual([]);
}

// Tablet, laptop and desktop widths (390px is covered by mobile.spec.ts).
for (const width of [768, 1280, 1440]) {
  test(`no horizontal overflow on any view at ${width}px`, async ({ page, request }) => {
    await requireBackend(request);
    await resetImports(request);
    const imp = await seed(request, "spring_boot.log");
    await page.setViewportSize({ width, height: 900 });
    const urls = [`/?import=${imp.id}`, "/import", `/imports/${imp.id}/incidents`];
    for (const url of urls) {
      await page.goto(url);
      await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
      if (url.startsWith("/?")) {
        await expect(page.locator("svg.chart-svg rect.chart-bar").first()).toBeVisible();
        await statStripsFilled(page, `overview at ${width}px`);
      }
      else if (url.endsWith("/incidents")) await expect(page.locator("[data-testid=incidents-table] tbody tr").first()).toBeVisible();
      else await expect(page.getByTestId("imports-table")).toBeVisible();
      const { sw, iw } = await page.evaluate(() => ({ sw: document.documentElement.scrollWidth, iw: document.documentElement.clientWidth }));
      expect(sw, `${url} scrolls horizontally at ${width}px (${sw} > ${iw})`).toBeLessThanOrEqual(iw);
    }
    await page.goto(`/imports/${imp.id}/incidents`);
    await page.locator(".title-link").first().click();
    await expect(page.getByRole("heading", { name: /Investigation notes/ })).toBeVisible();
    await expect(page.locator(".trace-body").first()).toBeVisible();
    await statStripsFilled(page, `investigation at ${width}px`);
    const { sw, iw } = await page.evaluate(() => ({ sw: document.documentElement.scrollWidth, iw: document.documentElement.clientWidth }));
    expect(sw, `investigation scrolls horizontally at ${width}px (${sw} > ${iw})`).toBeLessThanOrEqual(iw);
  });
}
