import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";
import { AUTH_PORT } from "../playwright.config";
import { chooseFile, requireBackend, resetImports, seed, setTheme } from "./helpers";

test.beforeEach(async ({ request }) => {
  await requireBackend(request);
  await resetImports(request);
});

async function scan(page: Page, label: string): Promise<void> {
  const results = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa", "best-practice"]).analyze();
  const blocking = results.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
  const summary = blocking.map((v) => `${v.id} (${v.impact}): ${v.help}\n  ${v.nodes.slice(0, 3).map((n) => n.target.join(" ")).join("\n  ")}`);
  expect(summary, `axe serious/critical violations on ${label}`).toEqual([]);
  const minor = results.violations.filter((v) => v.impact !== "serious" && v.impact !== "critical");
  expect(minor.map((v) => `${v.id}: ${v.help}`), `axe moderate/minor violations on ${label}`).toEqual([]);
}

for (const theme of ["light", "dark"] as const) {
  test(`(h) axe: all four views, ${theme} theme`, async ({ page, request }) => {
    const imp = await seed(request, "spring_boot.log");
    await seed(request, "telemetry.csv");
    await setTheme(page, theme);
    await page.emulateMedia({ colorScheme: theme });

    await page.goto("/");
    await expect(page.getByRole("heading", { name: "Unusual periods" })).toBeVisible();
    await expect(page.locator("svg.chart-svg").first()).toBeVisible();
    await scan(page, `Overview (${theme})`);

    // hovered chart tooltip + Metrics panel (telemetry is the latest import)
    await page.locator(".chart-svg").first().hover({ position: { x: 300, y: 100 } });
    await expect(page.locator(".chart-tip").first()).toBeVisible();
    await expect(page.getByRole("heading", { name: "Metrics" })).toBeVisible();
    await scan(page, `Overview with tooltip (${theme})`);

    await page.goto("/import");
    await expect(page.getByTestId("imports-table")).toBeVisible();
    await scan(page, `Import (${theme})`);

    await chooseFile(page, "node_express.log");
    await page.getByRole("button", { name: "Upload and analyse" }).click();
    await expect(page.getByRole("region", { name: "Ingestion summary" })).toBeVisible();
    await scan(page, `Import result (${theme})`);

    await page.getByRole("button", { name: /Delete telemetry\.csv/ }).click();
    await expect(page.getByRole("dialog")).toBeVisible();
    await scan(page, `Delete dialog (${theme})`);
    await page.keyboard.press("Escape");

    await page.goto(`/imports/${imp.id}/incidents`);
    await expect(page.locator("[data-testid=incidents-table] tbody tr")).toHaveCount(4);
    await scan(page, `Incidents (${theme})`);

    await page.getByLabel("Search").fill("NullPointer");
    await expect(page.locator("mark").first()).toBeVisible();
    await scan(page, `Incidents with search highlight (${theme})`);

    await page.getByLabel("Search").fill("");
    await expect(page.locator("[data-testid=incidents-table] tbody tr")).toHaveCount(4);
    await page.getByRole("link", { name: /SQLTransientConnectionException/ }).first().click();
    await expect(page.getByLabel("Stack trace", { exact: true }).first()).toBeVisible();
    await page.locator(".event").first().getByRole("button", { name: "Details" }).click();
    await page.getByRole("button", { name: /Show all frames/ }).first().click();
    await scan(page, `Investigation (${theme})`);
  });
}

test("axe: empty states and error states", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Nothing imported yet" })).toBeVisible();
  await scan(page, "Overview empty");
  await page.goto("/import");
  await expect(page.getByText("No imports yet").first()).toBeVisible();
  await scan(page, "Import empty");
  await page.goto("/imports/424242/incidents");
  await expect(page.getByRole("heading", { name: "That import no longer exists" })).toBeVisible();
  await scan(page, "Incidents not found");
});

test("axe: API key dialog", async ({ page }) => {
  await page.goto(`http://127.0.0.1:${AUTH_PORT}/`);
  await expect(page.getByRole("dialog", { name: "API key required" })).toBeVisible();
  await scan(page, "API key dialog");
});
