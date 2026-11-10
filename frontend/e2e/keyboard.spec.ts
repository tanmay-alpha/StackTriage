import { expect, test } from "@playwright/test";
import type { Locator, Page } from "@playwright/test";
import { requireBackend, resetImports, seed } from "./helpers";

/** Press Tab until `target` has focus; fails loudly if it is not reachable. */
async function tabTo(page: Page, target: Locator, max = 60): Promise<void> {
  for (let i = 0; i < max; i++) {
    await page.keyboard.press("Tab");
    if (await target.first().evaluate((el) => el === document.activeElement).catch(() => false)) return;
  }
  throw new Error(`could not reach ${String(target)} by keyboard in ${max} Tab presses`);
}

test.beforeEach(async ({ request }) => {
  await requireBackend(request);
  await resetImports(request);
});

test("(f) keyboard only: skip link, filters, sorting, opening an incident, status change, chart inspection", async ({ page, request }) => {
  const imp = await seed(request, "spring_boot.log");
  await page.goto(`/imports/${imp.id}/incidents`);
  await expect(page.locator("[data-testid=incidents-table] tbody tr")).toHaveCount(4);

  // skip link is the first stop and moves focus to <main>
  await page.keyboard.press("Tab");
  const skip = page.getByRole("link", { name: "Skip to main content" });
  await expect(skip).toBeFocused();
  await expect(skip).toBeVisible();
  await page.keyboard.press("Enter");
  expect(await page.evaluate(() => document.activeElement?.id)).toBe("main");

  // search with keyboard, results update
  await tabTo(page, page.getByLabel("Search"));
  await page.keyboard.type("NullPointer");
  await expect(page.locator("[data-testid=incidents-table] tbody tr")).toHaveCount(2);

  // level select via keyboard (ArrowDown changes the value of a focused select)
  await tabTo(page, page.getByLabel("Minimum level"));
  await page.keyboard.press("ArrowDown"); // Info and above
  await page.keyboard.press("ArrowDown"); // Warning and above
  await page.keyboard.press("ArrowDown"); // Error and above
  await expect(page).toHaveURL(/level=error/);

  // clear filters with the keyboard
  await tabTo(page, page.getByRole("button", { name: "Clear filters" }));
  await page.keyboard.press("Enter");
  await expect(page.locator("[data-testid=incidents-table] tbody tr")).toHaveCount(4);
  await expect(page).not.toHaveURL(/level=|q=/);

  // sortable header reachable and operable
  await tabTo(page, page.getByRole("button", { name: /^First seen/ }));
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/sort=first_seen/);
  await expect(page.locator("th[aria-sort=ascending], th[aria-sort=descending]")).toHaveCount(1);

  // inline status select reachable and changeable from the keyboard
  await tabTo(page, page.locator(".status-select"));
  const startValue = await page.evaluate(() => (document.activeElement as HTMLSelectElement).value);
  await page.keyboard.press(startValue === "open" ? "ArrowDown" : "ArrowUp");
  await expect(page.getByRole("status").filter({ hasText: /Status set to/ })).toBeVisible();

  // open an incident with Enter
  await tabTo(page, page.locator(".title-link"));
  const label = (await page.evaluate(() => document.activeElement?.getAttribute("title"))) ?? "";
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/\/incidents\/[0-9a-f]+$/);
  await expect(page.getByRole("heading", { level: 1 })).toHaveText(label);
  // focus moved to the new page content
  expect(await page.evaluate(() => document.activeElement?.id)).toBe("main");

  // the chart is a focusable group that exposes each window to the keyboard
  await tabTo(page, page.locator(".chart-focus"));
  await expect(page.locator(".chart-tip")).toBeVisible();
  const first = await page.locator(".chart-tip-title").innerText();
  await page.keyboard.press("ArrowRight");
  await expect(page.locator(".chart-tip-title")).not.toHaveText(first);
  await page.keyboard.press("End");
  await expect(page.locator(".chart-tip")).toBeVisible();

  // expandable record details are buttons with aria-expanded
  await tabTo(page, page.getByRole("button", { name: "Details" }));
  await page.keyboard.press("Enter");
  await expect(page.locator(".event").first().getByRole("button", { name: "Hide details" })).toHaveAttribute("aria-expanded", "true");
});

test("dialogs trap focus, close on Escape and restore focus", async ({ page, request }) => {
  await seed(request, "node_express.log");
  await page.goto("/import");
  const del = page.getByRole("button", { name: "Delete node_express.log" });
  await del.focus();
  await page.keyboard.press("Enter");
  const dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible();
  const inside = async () => page.evaluate(() => Boolean(document.activeElement?.closest("dialog")));
  for (let i = 0; i < 6; i++) {
    await page.keyboard.press("Tab");
    expect(await inside()).toBe(true);
  }
  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();
  await expect(del).toBeFocused();
});
