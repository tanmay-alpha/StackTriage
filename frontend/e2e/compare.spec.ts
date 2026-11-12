import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";
import type { APIRequestContext, Locator, Page } from "@playwright/test";
import { requireBackend, resetImports, seed, setTheme } from "./helpers";

interface Pair {
  before: number;
  after: number;
}

interface CompareItemRow {
  fingerprint: string;
  title: string;
  category: string;
  change: string | null;
}

test.beforeEach(async ({ request }) => {
  await requireBackend(request);
  await resetImports(request);
});

async function seedPair(request: APIRequestContext): Promise<Pair> {
  const before = await seed(request, "deploy_before.log");
  const after = await seed(request, "deploy_after.log");
  return { before: before.id, after: after.id };
}

const pairUrl = ({ before, after }: Pair) => `/compare?baseline=${before}&target=${after}`;
const rows = (page: Page) => page.locator("[data-testid=compare-table] tbody tr");
const row = (page: Page, text: string | RegExp) => rows(page).filter({ hasText: text });
const chip = (page: Page, name: RegExp) => page.getByRole("group", { name: "Show findings" }).getByRole("button", { name });
const chipCount = (page: Page, name: RegExp) => chip(page, name).locator(".cmp-chip-n");
const baselineSelect = (page: Page) => page.getByRole("combobox", { name: "Baseline (before)" });
const targetSelect = (page: Page) => page.getByRole("combobox", { name: "Target (after)" });

/** Press Tab until `target` has focus; fails loudly if it is not reachable. */
async function tabTo(page: Page, target: Locator, max = 80): Promise<void> {
  for (let i = 0; i < max; i++) {
    await page.keyboard.press("Tab");
    if (await target.first().evaluate((el) => el === document.activeElement).catch(() => false)) return;
  }
  throw new Error(`could not reach ${String(target)} by keyboard in ${max} Tab presses`);
}

async function noHorizontalOverflow(page: Page, where: string) {
  const { sw, iw } = await page.evaluate(() => ({ sw: document.documentElement.scrollWidth, iw: document.documentElement.clientWidth }));
  expect(sw, `${where}: page must not scroll horizontally (scrollWidth ${sw} > ${iw})`).toBeLessThanOrEqual(iw);
}

/** Minimal RFC 4180 parser (quoted fields, doubled quotes, CRLF). */
function parseCsv(text: string): string[][] {
  const out: string[][] = [];
  let rowCells: string[] = [];
  let cell = "";
  let quoted = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (quoted) {
      if (c === '"' && text[i + 1] === '"') {
        cell += '"';
        i++;
      } else if (c === '"') quoted = false;
      else cell += c;
    } else if (c === '"') quoted = true;
    else if (c === ",") {
      rowCells.push(cell);
      cell = "";
    } else if (c === "\n" || c === "\r") {
      if (c === "\r" && text[i + 1] === "\n") i++;
      rowCells.push(cell);
      out.push(rowCells);
      rowCells = [];
      cell = "";
    } else cell += c;
  }
  if (cell !== "" || rowCells.length) {
    rowCells.push(cell);
    out.push(rowCells);
  }
  return out;
}

test("journey: preselected before/after pair, totals, New filter, export and investigation link", async ({ page, request }) => {
  const pair = await seedPair(request);
  await page.goto("/");
  await page.getByRole("link", { name: "Compare", exact: true }).click();

  // two most recent imports: older = baseline, newer = target, written to the URL
  await expect(page).toHaveURL(new RegExp(`/compare\\?baseline=${pair.before}&target=${pair.after}$`));
  await expect(baselineSelect(page)).toHaveValue(String(pair.before));
  await expect(targetSelect(page)).toHaveValue(String(pair.after));
  await expect(page.getByRole("heading", { name: "Baseline capture (before)" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Target capture (after)" })).toBeVisible();
  await expect(page.locator(".cmp-side").first()).toContainText("deploy_before.log");
  await expect(page.locator(".cmp-side").nth(1)).toContainText("deploy_after.log");

  await expect(chipCount(page, /^New\b/)).toHaveText("2");
  await expect(chipCount(page, /^Increased\b/)).toHaveText("2");
  await expect(chipCount(page, /^Decreased\b/)).toHaveText("1");
  await expect(chipCount(page, /^Disappeared\b/)).toHaveText("2");
  await expect(chipCount(page, /^Recurring\b/)).toHaveText("5");
  await expect(chipCount(page, /^All\b/)).toHaveText("9");
  await expect(rows(page)).toHaveCount(9);

  await chip(page, /^New\b/).click();
  await expect(page).toHaveURL(/category=new/);
  await expect(chip(page, /^New\b/)).toHaveAttribute("aria-pressed", "true");
  await expect(rows(page)).toHaveCount(2);
  await expect(row(page, "AttributeError: 'NoneType' object has no attribute 'round'")).toHaveCount(1);
  await expect(row(page, /LookupError: no tax table loaded/)).toHaveCount(1);
  for (const r of await rows(page).all()) await expect(r.locator("td[data-label=Finding]")).toContainText("New");

  // back/forward restores the filter
  await page.goBack();
  await expect(page).not.toHaveURL(/category=/);
  await expect(rows(page)).toHaveCount(9);

  // export through the UI with the current basis
  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: "Export comparison as CSV" }).click();
  expect((await download).suggestedFilename()).toBe(`stacktriage-compare-${pair.before}-vs-${pair.after}.csv`);

  const timeout = row(page, /TimeoutError: payment gateway/);
  await expect(timeout.locator("td[data-label=Finding]")).toContainText("Increased");
  await timeout.locator(".title-link").click();
  await expect(page).toHaveURL(new RegExp(`/imports/${pair.after}/incidents/[0-9a-f]+$`));
  await expect(page.getByRole("heading", { level: 1 })).toContainText("TimeoutError");
});

test("entry points prefill the current import as target", async ({ page, request }) => {
  const pair = await seedPair(request);
  await page.goto(`/imports/${pair.after}/incidents`);
  await page.getByRole("link", { name: "Compare with another import" }).click();
  await expect(page).toHaveURL(new RegExp(`baseline=${pair.before}&target=${pair.after}`));
  await page.goto(`/?import=${pair.before}`);
  await page.getByRole("link", { name: "Compare with another import" }).click();
  // the baseline import is the oldest one, so the other import becomes the baseline
  await expect(page).toHaveURL(new RegExp(`baseline=${pair.after}&target=${pair.before}`));
});

test("swap exchanges the ids in the URL and turns a new group into a disappeared one", async ({ page, request }) => {
  const pair = await seedPair(request);
  await page.goto(pairUrl(pair));
  const currency = row(page, /no attribute 'round'/);
  await expect(currency.locator("td[data-label=Finding]")).toContainText("New");

  await page.getByRole("button", { name: "Swap baseline and target" }).click();
  await expect(page).toHaveURL(new RegExp(`baseline=${pair.after}&target=${pair.before}`));
  await expect(baselineSelect(page)).toHaveValue(String(pair.after));
  await expect(currency.locator("td[data-label=Finding]")).toContainText("Disappeared");
  await expect(currency.locator("td[data-label=Change]")).toContainText("absent from target");
  await expect(chipCount(page, /^Disappeared\b/)).toHaveText("2");
  await expect(chipCount(page, /^New\b/)).toHaveText("2");

  await page.goBack();
  await expect(page).toHaveURL(new RegExp(`baseline=${pair.before}&target=${pair.after}`));
  await expect(currency.locator("td[data-label=Finding]")).toContainText("New");
});

test("a group marked resolved but present in the target is flagged and filterable", async ({ page, request }) => {
  const pair = await seedPair(request);
  const res = await request.get(`/api/imports/${pair.before}/compare/${pair.after}?limit=200`);
  expect(res.ok()).toBeTruthy();
  const body: { items: CompareItemRow[] } = await res.json();
  const items = body.items;
  const inventory = items.find((i) => i.title.startsWith("ConnectionError") && i.title.includes("warehouse"));
  expect(inventory, "inventory ConnectionError group").toBeTruthy();

  await page.goto(pairUrl(pair));
  await expect(chipCount(page, /^Marked resolved/)).toHaveText("0");
  const put = await request.put(`/api/incidents/${inventory!.fingerprint}/status`, { data: { status: "resolved" } });
  expect(put.ok()).toBeTruthy();
  try {
    await page.reload();
    await expect(chipCount(page, /^Marked resolved/)).toHaveText("1");
    const r = row(page, /warehouse API refused connection/);
    await expect(r.locator("td[data-label=Status]")).toContainText("Resolved");
    await expect(r.locator("td[data-label=Status]")).toContainText("Marked resolved, observed in target");
    await chip(page, /^Marked resolved/).click();
    await expect(page).toHaveURL(/category=resolved_observed/);
    await expect(rows(page)).toHaveCount(1);
    await expect(rows(page).first()).toContainText("warehouse API refused connection");
  } finally {
    await request.put(`/api/incidents/${inventory!.fingerprint}/status`, { data: { status: "open" } });
  }
});

test("per-minute basis: available for this pair, reclassifies the pool row and notes the disagreement", async ({ page, request }) => {
  const pair = await seedPair(request);
  await page.goto(pairUrl(pair));
  const pool = row(page, /connection pool exhausted/);
  await expect(pool.locator("td[data-label=Finding]")).toContainText("No clear change");
  await expect(pool.locator(".cmp-note")).toContainText("Per-minute rate tells a different story");
  await expect(pool.locator(".cmp-note")).toHaveAttribute("title", /traffic volume or capture length changed/);

  const perMinute = page.getByRole("button", { name: "Per minute", exact: true });
  await expect(perMinute).toBeEnabled();
  await perMinute.click();
  await expect(page).toHaveURL(/basis=time/);
  await expect(perMinute).toHaveAttribute("aria-pressed", "true");
  await expect(pool.locator("td[data-label=Finding]")).toContainText("Increased");
  await expect(pool.locator(".cmp-note")).toContainText("tells a different story");
  await expect(pool.locator("td[data-label=Before]")).toContainText("per min");
});

test("same import on both sides shows a message instead of calling the API", async ({ page, request }) => {
  const pair = await seedPair(request);
  const compareCalls: string[] = [];
  const failures: string[] = [];
  page.on("request", (r) => {
    if (r.url().includes("/compare/")) compareCalls.push(r.url());
  });
  page.on("response", (r) => {
    if (r.url().includes("/api/") && r.status() >= 400) failures.push(`${r.status()} ${r.url()}`);
  });
  await page.goto(pairUrl(pair));
  await expect(rows(page).first()).toBeVisible();

  await targetSelect(page).selectOption(String(pair.before));
  await expect(page).toHaveURL(new RegExp(`baseline=${pair.before}&target=${pair.before}`));
  await expect(page.getByText("Choose two different imports")).toBeVisible();
  await expect(page.locator(".state-error")).toHaveCount(0);
  await expect(page.getByTestId("compare-table")).toHaveCount(0);
  expect(compareCalls.filter((u) => u.includes(`/imports/${pair.before}/compare/${pair.before}`))).toEqual([]);
  expect(failures).toEqual([]);
});

test("unknown import shows the server error with a retry", async ({ page, request }) => {
  const pair = await seedPair(request);
  await page.goto(`/compare?baseline=${pair.before}&target=424242`);
  await expect(page.getByRole("alert")).toContainText("import not found");
  await expect(page.getByRole("button", { name: "Retry" })).toBeVisible();
});

test("fewer than two imports shows an empty state with a link to Import", async ({ page, request }) => {
  await seed(request, "deploy_before.log");
  await page.goto("/compare");
  await expect(page.getByRole("heading", { name: "Two imports are needed to compare" })).toBeVisible();
  await page.getByRole("link", { name: "Import a log file" }).click();
  await expect(page).toHaveURL(/\/import$/);
});

test("CSV export: header, content type and formula cells neutralised", async ({ request }) => {
  const pair = await seedPair(request);
  const res = await request.get(`/api/imports/${pair.before}/compare/${pair.after}/export?format=csv&basis=events`);
  expect(res.status()).toBe(200);
  expect(res.headers()["content-type"]).toContain("text/csv");
  expect(res.headers()["content-disposition"]).toContain(`stacktriage-compare-${pair.before}-vs-${pair.after}.csv`);
  const [header] = parseCsv(await res.text());
  expect(header).toContain("category");

  const line = '2026-03-05 10:00:00,000 - app - ERROR - =HYPERLINK("x")\n';
  const a = await seed(request, { name: "formula_a.log", content: Buffer.from(line) });
  const b = await seed(request, { name: "formula_b.log", content: Buffer.from(line + line.replace("10:00:00", "10:00:05")) });
  const exp = await request.get(`/api/imports/${a.id}/compare/${b.id}/export?format=csv`);
  expect(exp.status()).toBe(200);
  const [cols, ...data] = parseCsv(await exp.text());
  const title = cols!.indexOf("title");
  expect(title).toBeGreaterThanOrEqual(0);
  const hit = data.find((r) => r[title]?.includes("HYPERLINK"));
  expect(hit, "formula row present in export").toBeTruthy();
  expect(hit![title]!.startsWith("'=")).toBe(true);
  for (const r of data) for (const c of r) expect(c.startsWith("="), `cell ${c} starts with =`).toBe(false);
});

test("keyboard: swap with Enter and reach a result link", async ({ page, request }) => {
  const pair = await seedPair(request);
  await page.goto(pairUrl(pair));
  await expect(rows(page)).toHaveCount(9);

  await tabTo(page, page.getByRole("button", { name: "Swap baseline and target" }));
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(new RegExp(`baseline=${pair.after}&target=${pair.before}`));
  await expect(rows(page)).toHaveCount(9);

  // filter chips are buttons operable from the keyboard
  await tabTo(page, chip(page, /^Disappeared\b/));
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/category=disappeared/);
  await expect(rows(page)).toHaveCount(2);

  await tabTo(page, rows(page).first().locator(".title-link"));
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/\/incidents\/[0-9a-f]+$/);
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
  test(`axe: compare page, ${theme} theme`, async ({ page, request }) => {
    const pair = await seedPair(request);
    await setTheme(page, theme);
    await page.emulateMedia({ colorScheme: theme });
    await page.goto(pairUrl(pair));
    await expect(rows(page)).toHaveCount(9);
    await page.getByText("How to read this").click();
    await expect(page.locator(".cmp-help-body")).toBeVisible();
    await scan(page, `Compare (${theme})`);

    await page.getByRole("button", { name: "Per minute", exact: true }).click();
    await chip(page, /^Increased\b/).click();
    await expect(page).toHaveURL(/category=increased/);
    await expect(rows(page).first().locator("td[data-label=Finding]")).toContainText("Increased");
    await scan(page, `Compare per minute, filtered (${theme})`);

    await targetSelect(page).selectOption(String(pair.before));
    await expect(page.getByText("Choose two different imports")).toBeVisible();
    await scan(page, `Compare same import (${theme})`);
  });
}

for (const vp of [
  { width: 390, height: 844 },
  { width: 768, height: 900 },
]) {
  test(`responsive ${vp.width}px: no horizontal overflow, labelled cards`, async ({ page, request }) => {
    const pair = await seedPair(request);
    await page.setViewportSize(vp);
    await page.goto(pairUrl(pair));
    await expect(rows(page)).toHaveCount(9);
    await noHorizontalOverflow(page, `compare at ${vp.width}px`);

    const card = rows(page).first();
    expect((await card.boundingBox())!.width).toBeGreaterThan(vp.width / 2);
    for (const label of ["Finding", "Service", "Before", "After", "Change", "Evidence", "Status"]) {
      const cell = card.locator(`td[data-label="${label}"]`);
      await expect(cell).toBeVisible();
      expect(await cell.evaluate((td) => getComputedStyle(td, "::before").content)).toBe(`"${label}"`);
    }

    await page.getByRole("button", { name: "Per minute", exact: true }).click();
    await expect(page).toHaveURL(/basis=time/);
    await expect(row(page, /connection pool exhausted/).locator(".cmp-note")).toBeVisible();
    await page.getByText("How to read this").click();
    await noHorizontalOverflow(page, `compare per minute at ${vp.width}px`);

    if (vp.width < 500) {
      await page.getByRole("button", { name: "Menu" }).click();
      await expect(page.getByRole("link", { name: "Compare", exact: true })).toBeVisible();
    }
  });
}

test("imports outside the fetched import list stay selected and labelled", async ({ page, request }) => {
  const pair = await seedPair(request);
  // Simulate more than 200 imports: the list page the UI fetches no longer contains the baseline.
  await page.route("**/api/imports?*", async (route) => {
    const res = await route.fetch();
    const body = (await res.json()) as { items: { id: number }[]; total: number };
    body.items = body.items.filter((i) => i.id !== pair.before);
    body.items.push({ ...body.items[0], id: 999_999, filename: "unrelated.log" } as (typeof body.items)[number]);
    await route.fulfill({ response: res, json: body });
  });
  await page.goto(pairUrl(pair));
  await expect(rows(page).first()).toBeVisible();
  await expect(baselineSelect(page)).toHaveValue(String(pair.before));
  await expect(baselineSelect(page).locator("option:checked")).toContainText("deploy_before.log");
  await expect(page.getByText(/not found/)).toHaveCount(0);
});
