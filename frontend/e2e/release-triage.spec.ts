import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";
import type { APIRequestContext, Page } from "@playwright/test";
import { requireBackend, resetImports, seed, setTheme } from "./helpers";

interface Seeded {
  before: number;
  after: number;
  /** the warehouse ConnectionError group: present in both captures (target 12:00-13:00 UTC) */
  fingerprint: string;
}

let current: Seeded | null = null;

test.beforeEach(async ({ request, page }) => {
  await requireBackend(request);
  await resetImports(request);
  // fix times below are entered and asserted in UTC
  await page.addInitScript(() => localStorage.setItem("stacktriage.tz", "utc"));
});

// triage state is per fingerprint and survives import deletion: leave the group open for other specs
test.afterEach(async ({ request }) => {
  if (current) await request.put(`/api/incidents/${current.fingerprint}/status`, { data: { status: "open" } });
  current = null;
});

async function seedPair(request: APIRequestContext): Promise<Seeded> {
  const before = await seed(request, "deploy_before.log");
  const after = await seed(request, "deploy_after.log");
  const res = await request.get(`/api/imports/${before.id}/compare/${after.id}?limit=200`);
  expect(res.ok()).toBeTruthy();
  const body = (await res.json()) as { items: { fingerprint: string; title: string }[] };
  const group = body.items.find((i) => i.title.startsWith("ConnectionError") && i.title.includes("warehouse"));
  expect(group, "warehouse ConnectionError group").toBeTruthy();
  current = { before: before.id, after: after.id, fingerprint: group!.fingerprint };
  return current;
}

async function recordFix(request: APIRequestContext, fingerprint: string, fixedAt: string, version: string): Promise<void> {
  const res = await request.put(`/api/incidents/${fingerprint}/status`, { data: { status: "resolved", fixed_at: fixedAt, fixed_version: version } });
  expect(res.ok(), `recording the fix failed with ${res.status()}`).toBeTruthy();
}

const investigateUrl = (s: Seeded) => `/imports/${s.after}/incidents/${encodeURIComponent(s.fingerprint)}`;
const compareUrl = (s: Seeded) => `/compare?baseline=${s.before}&target=${s.after}`;
const fixPanel = (page: Page) => page.getByRole("region", { name: "Fix record" });
const rows = (page: Page) => page.locator("[data-testid=compare-table] tbody tr");
const chip = (page: Page, name: RegExp) => page.getByRole("group", { name: "Show findings" }).getByRole("button", { name });

async function noHorizontalOverflow(page: Page, where: string) {
  const { sw, iw } = await page.evaluate(() => ({ sw: document.documentElement.scrollWidth, iw: document.documentElement.clientWidth }));
  expect(sw, `${where}: page must not scroll horizontally (scrollWidth ${sw} > ${iw})`).toBeLessThanOrEqual(iw);
}

async function scan(page: Page, label: string): Promise<void> {
  const results = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa", "best-practice"]).analyze();
  const blocking = results.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
  const summary = blocking.map((v) => `${v.id} (${v.impact}): ${v.help}\n  ${v.nodes.slice(0, 3).map((n) => n.target.join(" ")).join("\n  ")}`);
  expect(summary, `axe serious/critical violations on ${label}`).toEqual([]);
  const minor = results.violations.filter((v) => v.impact !== "serious" && v.impact !== "critical");
  expect(minor.map((v) => `${v.id}: ${v.help}`), `axe moderate/minor violations on ${label}`).toEqual([]);
}

test("investigation: record a fix through the UI, it persists, and leaving resolved clears it", async ({ page, request }) => {
  const s = await seedPair(request);
  await page.goto(investigateUrl(s));
  const statusSelect = page.getByRole("combobox", { name: /^Status:/ });
  await expect(statusSelect).toHaveValue("open");
  await expect(fixPanel(page)).toHaveCount(0);

  await statusSelect.selectOption("resolved");
  await expect(page.getByRole("status").filter({ hasText: /Status set to resolved/ })).toBeVisible();
  const panel = fixPanel(page);
  await expect(panel).toBeVisible();
  await expect(panel.getByTestId("fix-record")).toHaveText("No fix recorded yet.");
  await expect(panel.getByRole("button", { name: "Clear" })).toBeDisabled();

  // the time is entered in the selected zone, which the label names
  await panel.getByLabel("Fix shipped at (UTC)").fill("2026-03-05T11:30");
  await panel.getByLabel("Release / version").fill("2026.03.05-1");
  await panel.getByRole("button", { name: "Save fix record" }).click();
  await expect(page.getByRole("status").filter({ hasText: "Fix record saved." })).toBeVisible();
  await expect(panel.getByTestId("fix-record")).toContainText("2026-03-05 11:30:00 UTC");
  await expect(panel.getByTestId("fix-record")).toContainText("2026.03.05-1");

  const detail = (await (await request.get(`/api/imports/${s.after}/incidents/${s.fingerprint}`)).json()) as { fixed_at: string; fixed_version: string };
  expect(Date.parse(detail.fixed_at)).toBe(Date.parse("2026-03-05T11:30:00Z"));
  expect(detail.fixed_version).toBe("2026.03.05-1");

  await page.reload();
  await expect(fixPanel(page).getByTestId("fix-record")).toContainText("2026-03-05 11:30:00 UTC");
  await expect(fixPanel(page).getByTestId("fix-record")).toContainText("2026.03.05-1");
  await expect(fixPanel(page).getByLabel("Fix shipped at (UTC)")).toHaveValue(/^2026-03-05T11:30/);
  await expect(fixPanel(page).getByLabel("Release / version")).toHaveValue("2026.03.05-1");

  // a server-side validation error is shown next to the form
  await fixPanel(page).getByLabel("Fix shipped at (UTC)").fill("2150-01-01T00:00");
  await fixPanel(page).getByRole("button", { name: "Save fix record" }).click();
  await expect(fixPanel(page).getByRole("alert")).toContainText("between 1970 and 2100");
  await expect(fixPanel(page).getByTestId("fix-record")).toContainText("2026-03-05 11:30:00 UTC");

  // leaving resolved clears the record on the server; the panel and record are gone
  await page.getByRole("combobox", { name: /^Status:/ }).selectOption("investigating");
  await expect(page.getByRole("status").filter({ hasText: /Status set to investigating/ })).toBeVisible();
  await expect(fixPanel(page)).toHaveCount(0);
  await page.getByRole("combobox", { name: /^Status:/ }).selectOption("resolved");
  await expect(fixPanel(page).getByTestId("fix-record")).toHaveText("No fix recorded yet.");
  await page.reload();
  await expect(fixPanel(page).getByTestId("fix-record")).toHaveText("No fix recorded yet.");
  const cleared = (await (await request.get(`/api/imports/${s.after}/incidents/${s.fingerprint}`)).json()) as { fixed_at: string | null; fixed_version: string | null };
  expect(cleared.fixed_at).toBeNull();
  expect(cleared.fixed_version).toBeNull();
});

test("investigation: Clear removes a recorded fix and keeps the group resolved", async ({ page, request }) => {
  const s = await seedPair(request);
  await recordFix(request, s.fingerprint, "2026-03-05T11:30:00Z", "2026.03.05-1");
  await page.goto(investigateUrl(s));
  await expect(fixPanel(page).getByTestId("fix-record")).toContainText("2026.03.05-1");
  await fixPanel(page).getByRole("button", { name: "Clear" }).click();
  await expect(page.getByRole("status").filter({ hasText: "Fix record cleared." })).toBeVisible();
  await expect(fixPanel(page).getByTestId("fix-record")).toHaveText("No fix recorded yet.");
  await expect(page.getByRole("combobox", { name: /^Status:/ })).toHaveValue("resolved");
});

test("investigation: saving with the time untouched keeps the stored instant exactly (no fixed_at sent)", async ({ page, request }) => {
  const s = await seedPair(request);
  await recordFix(request, s.fingerprint, "2026-03-05T11:30:00.250Z", "2026.03.05-1");
  await page.goto(investigateUrl(s));
  await expect(page.getByRole("complementary", { name: "Fix record, notes, related incidents and export" })).toBeVisible();
  await expect(fixPanel(page).getByTestId("fix-record")).toContainText("2026.03.05-1");

  await fixPanel(page).getByLabel("Release / version").fill("2026.03.05-2");
  const put = page.waitForRequest((r) => r.method() === "PUT" && r.url().includes("/status"));
  await fixPanel(page).getByRole("button", { name: "Save fix record" }).click();
  const sent = (await put).postDataJSON() as Record<string, unknown>;
  expect(sent).toEqual({ status: "resolved", fixed_version: "2026.03.05-2" });
  await expect(fixPanel(page).getByTestId("fix-record")).toContainText("2026.03.05-2");

  const detail = (await (await request.get(`/api/imports/${s.after}/incidents/${s.fingerprint}`)).json()) as { fixed_at: string; fixed_version: string };
  expect(Date.parse(detail.fixed_at)).toBe(Date.parse("2026-03-05T11:30:00.250Z"));
  expect(detail.fixed_version).toBe("2026.03.05-2");

  // emptying the time field sends an explicit null
  await fixPanel(page).getByLabel("Fix shipped at (UTC)").fill("");
  const put2 = page.waitForRequest((r) => r.method() === "PUT" && r.url().includes("/status"));
  await fixPanel(page).getByRole("button", { name: "Save fix record" }).click();
  expect((await put2).postDataJSON()).toEqual({ status: "resolved", fixed_at: null, fixed_version: "2026.03.05-2" });
  await expect(fixPanel(page).getByTestId("fix-record")).toContainText("not recorded");
});

test.describe("local time zone", () => {
  test.use({ timezoneId: "America/New_York" });

  test("investigation: local entry is labelled 'local time' with the zone at the entered instant", async ({ page, request }) => {
    const s = await seedPair(request);
    await page.addInitScript(() => localStorage.setItem("stacktriage.tz", "local"));
    expect((await request.put(`/api/incidents/${s.fingerprint}/status`, { data: { status: "resolved" } })).ok()).toBeTruthy();
    await page.goto(investigateUrl(s));
    const time = fixPanel(page).getByLabel("Fix shipped at (local time)");
    await expect(time).toBeVisible();

    // a January instant is EST, a July instant EDT, whatever today's abbreviation is
    await time.fill("2026-01-15T09:00");
    await expect(fixPanel(page).getByText(/Entered in your local time \(EST at that time\)/)).toBeVisible();
    await time.fill("2026-07-01T12:00");
    await expect(fixPanel(page).getByText(/Entered in your local time \(EDT at that time\)/)).toBeVisible();

    await fixPanel(page).getByRole("button", { name: "Save fix record" }).click();
    await expect(page.getByRole("status").filter({ hasText: "Fix record saved." })).toBeVisible();
    await expect(fixPanel(page).getByTestId("fix-record")).toContainText("2026-07-01 12:00:00 EDT");
    const detail = (await (await request.get(`/api/imports/${s.after}/incidents/${s.fingerprint}`)).json()) as { fixed_at: string };
    expect(Date.parse(detail.fixed_at)).toBe(Date.parse("2026-07-01T16:00:00Z"));
  });
});

test("compare: occurrences after the recorded fix are a regression, listed first and filterable", async ({ page, request }) => {
  const s = await seedPair(request);
  await recordFix(request, s.fingerprint, "2026-03-05T11:30:00Z", "2026.03.05-1");
  await page.goto(compareUrl(s));
  await expect(rows(page)).toHaveCount(9);
  await expect(chip(page, /^Regressed after fix\b/).locator(".cmp-chip-n")).toHaveText("1");

  // regressions sort first
  const first = rows(page).first();
  await expect(first).toContainText("warehouse API refused connection");
  const status = first.locator("td[data-label=Status]");
  await expect(status).toContainText("Regressed after fix");
  await expect(status).toContainText("40 after fix 2026.03.05-1 (2026-03-05 11:30:00 UTC)");
  await expect(status.locator(".badge", { hasText: "Regressed after fix" })).toHaveAttribute("title", /after the recorded fix/);
  await expect(status.locator(".badge", { hasText: "Regressed after fix" }).locator("svg")).toHaveCount(1);
  await expect(status).not.toContainText("Marked resolved, observed in target");

  await chip(page, /^Regressed after fix\b/).click();
  await expect(page).toHaveURL(/category=regressed/);
  await expect(chip(page, /^Regressed after fix\b/)).toHaveAttribute("aria-pressed", "true");
  await expect(rows(page)).toHaveCount(1);
  await expect(rows(page).first()).toContainText("warehouse API refused connection");
});

test("compare: a fix recorded after the target capture shows the occurrences as before the fix", async ({ page, request }) => {
  const s = await seedPair(request);
  await recordFix(request, s.fingerprint, "2026-03-05T14:00:00Z", "2026.03.05-2");
  await page.goto(compareUrl(s));
  await expect(rows(page)).toHaveCount(9);
  await expect(chip(page, /^Regressed after fix\b/).locator(".cmp-chip-n")).toHaveText("0");
  const status = rows(page).filter({ hasText: "warehouse API refused connection" }).locator("td[data-label=Status]");
  await expect(status).toContainText("Only before recorded fix");
  await expect(status.locator(".badge", { hasText: "Only before recorded fix" })).toHaveAttribute("title", /consistent with the fix/);
  await expect(status).not.toContainText("Regressed after fix");
});

test("compare: resolved without a fix time keeps the neutral flag with a hint", async ({ page, request }) => {
  const s = await seedPair(request);
  expect((await request.put(`/api/incidents/${s.fingerprint}/status`, { data: { status: "resolved" } })).ok()).toBeTruthy();
  await page.goto(compareUrl(s));
  const flag = rows(page).filter({ hasText: "warehouse API refused connection" }).locator("td[data-label=Status] .badge", { hasText: "Marked resolved, observed in target" });
  await expect(flag).toBeVisible();
  await expect(flag).toHaveAttribute("title", /Record when the fix shipped/);
});

for (const theme of ["light", "dark"] as const) {
  test(`axe: investigation with the fix panel, ${theme} theme`, async ({ page, request }) => {
    const s = await seedPair(request);
    await recordFix(request, s.fingerprint, "2026-03-05T11:30:00Z", "2026.03.05-1");
    await setTheme(page, theme);
    await page.emulateMedia({ colorScheme: theme });
    await page.goto(investigateUrl(s));
    await expect(fixPanel(page).getByTestId("fix-record")).toContainText("2026.03.05-1");
    await scan(page, `Investigation with fix record (${theme})`);

    // inline validation error state
    await fixPanel(page).getByLabel("Fix shipped at (UTC)").fill("2150-01-01T00:00");
    await fixPanel(page).getByRole("button", { name: "Save fix record" }).click();
    await expect(fixPanel(page).getByRole("alert")).toBeVisible();
    await scan(page, `Investigation with fix error (${theme})`);
  });
}

test("390px: no horizontal overflow on investigation with the fix panel and on compare with a regression", async ({ page, request }) => {
  const s = await seedPair(request);
  await recordFix(request, s.fingerprint, "2026-03-05T11:30:00Z", "2026.03.05-1");
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(investigateUrl(s));
  await expect(fixPanel(page).getByTestId("fix-record")).toContainText("2026.03.05-1");
  await fixPanel(page).scrollIntoViewIfNeeded();
  await noHorizontalOverflow(page, "investigation at 390px");

  await page.goto(compareUrl(s));
  await expect(rows(page).first()).toContainText("Regressed after fix");
  await noHorizontalOverflow(page, "compare at 390px");
});
