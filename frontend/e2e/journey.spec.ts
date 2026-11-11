import { readFileSync } from "node:fs";
import { expect, test } from "@playwright/test";
import { chooseFile, dropFile, fixture, requireBackend, resetImports, seed } from "./helpers";

test.beforeEach(async ({ request }) => {
  await requireBackend(request);
  await resetImports(request);
});

test("(a) empty state -> import by drag-and-drop -> overview -> incidents -> investigation -> persisted triage -> exports", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));

  // empty state
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1, name: "Overview" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Nothing imported yet" })).toBeVisible();
  await page.getByRole("link", { name: "Import a log file" }).click();
  await expect(page).toHaveURL(/\/import$/);

  // drag-and-drop the real Spring Boot fixture
  await dropFile(page, "spring_boot.log", readFileSync(fixture("spring_boot.log")));
  await expect(page.getByText("spring_boot.log").first()).toBeVisible();
  await page.getByRole("button", { name: "Upload and analyse" }).click();

  // ingestion summary
  const summary = page.getByRole("region", { name: "Ingestion summary" });
  await expect(summary).toBeVisible();
  await expect(summary.getByText("Format detected")).toBeVisible();
  await expect(summary.locator(".stat", { hasText: "Format detected" })).toContainText("java");
  await expect(summary.locator(".stat", { hasText: "Events parsed" })).toContainText("340");
  await expect(summary.locator(".stat", { hasText: "Error groups" })).toContainText("4");
  await expect(summary.getByRole("tab", { name: /Parse diagnostics/ })).toBeVisible();
  await expect(summary.getByRole("tab", { name: /Redactions/ })).toBeVisible();
  await expect(page.getByTestId("imports-table").getByText("spring_boot.log")).toBeVisible();

  // overview
  await summary.getByRole("link", { name: "Open Overview" }).click();
  await expect(page).toHaveURL(/\/\?import=\d+$/);
  await expect(page.getByRole("heading", { name: "Log volume over time" })).toBeVisible();
  await expect(page.getByRole("group", { name: /Log volume over time, 120 windows/ })).toBeVisible();
  await expect(page.locator("svg.chart-svg rect.chart-bar").first()).toBeVisible();
  await expect(page.getByRole("heading", { name: "Unusual periods" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Top error groups" })).toBeVisible();
  await expect(page.getByRole("link", { name: /SQLTransientConnectionException/ }).first()).toBeVisible();
  await expect(page.getByRole("heading", { name: "Parse diagnostics" })).toBeVisible();

  // incidents
  await page.getByRole("link", { name: "View incidents" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Incidents" })).toBeVisible();
  const rows = page.locator("[data-testid=incidents-table] tbody tr");
  await expect(rows).toHaveCount(4);

  // search (debounced, reflected in the URL)
  await page.getByLabel("Search").fill("NullPointer");
  await expect(rows).toHaveCount(2);
  await expect(page).toHaveURL(/[?&]q=NullPointer/);
  await expect(page.locator("mark").first()).toHaveText(/nullpointer/i);
  await page.getByLabel("Search").fill("");
  await expect(rows).toHaveCount(4);

  // filter: minimum level
  await page.getByLabel("Minimum level").selectOption("error");
  await expect(rows).toHaveCount(3);
  await expect(page).toHaveURL(/level=error/);
  await page.getByLabel("Minimum level").selectOption("");
  await expect(rows).toHaveCount(4);

  // filter: ongoing / ended
  await page.getByLabel("Activity").selectOption("ongoing");
  await expect(rows).toHaveCount(1);
  await expect(rows.first()).toContainText("Ongoing");
  await page.getByLabel("Activity").selectOption("ended");
  await expect(rows).toHaveCount(3);
  await page.getByLabel("Activity").selectOption("");

  // sort by count ascending
  await page.getByLabel("Sort by").selectOption("count");
  await page.getByRole("button", { name: "Descending" }).click();
  await expect(page).toHaveURL(/order=asc/);
  await expect
    .poll(async () => (await rows.locator("td[data-label=Count]").allInnerTexts()).map((c) => Number(c.replace(/,/g, ""))))
    .toEqual([5, 11, 17, 67]);

  // open the incident with the stack trace
  await page.getByRole("link", { name: /SQLTransientConnectionException/ }).first().click();
  await expect(page).toHaveURL(/\/imports\/\d+\/incidents\/[0-9a-f]+$/);
  await expect(page.getByRole("heading", { level: 1 })).toContainText("SQLTransientConnectionException");
  const trace = page.getByLabel("Stack trace", { exact: true }).first();
  await expect(trace).toContainText("DataAccessResourceFailureException");
  await expect(trace).toContainText("library frame");
  await page.getByRole("button", { name: /Show all frames/ }).first().click();
  await expect(trace).toContainText("HibernateJpaDialect.convertHibernateAccessException");
  await expect(page.getByRole("group", { name: /Occurrences of this incident per 60-second window/ })).toBeVisible();

  // change status, add a note, then reload and see both persisted
  const statusSelect = page.getByRole("combobox", { name: /^Status:/ });
  const before = await statusSelect.inputValue();
  const next = before === "investigating" ? "resolved" : "investigating";
  await statusSelect.selectOption(next);
  await expect(page.getByRole("status").filter({ hasText: /Status set to/ })).toBeVisible();
  const noteText = `Checked the Hikari pool settings ${Date.now()}`;
  await page.getByLabel("Add a note").fill(noteText);
  await page.getByRole("button", { name: "Add note" }).click();
  await expect(page.getByRole("list", { name: "Notes" })).toContainText(noteText);

  await page.reload();
  await expect(page.getByRole("combobox", { name: /^Status:/ })).toHaveValue(next);
  await expect(page.getByRole("list", { name: "Notes" })).toContainText(noteText);

  // records list with expandable details
  const firstRecord = page.locator(".event").first();
  await expect(firstRecord).toContainText("Unexpected error on request");
  await firstRecord.getByRole("button", { name: "Details" }).click();
  await expect(firstRecord.getByLabel("Stack trace", { exact: true })).toBeVisible();

  // related incidents panel exists
  await expect(page.getByRole("heading", { name: "Started around the same time" })).toBeVisible();

  // exports download through fetch -> Blob
  const csv = page.waitForEvent("download");
  await page.getByRole("button", { name: "Export report as CSV" }).click();
  const csvDl = await csv;
  expect(csvDl.suggestedFilename()).toMatch(/\.csv$/);
  const csvText = readFileSync((await csvDl.path())!, "utf8");
  expect(csvText).toContain("status,level,service,count");
  expect(csvText).toContain("SQLTransientConnectionException");

  const md = page.waitForEvent("download");
  await page.getByRole("button", { name: "Export report as Markdown" }).click();
  const mdDl = await md;
  expect(mdDl.suggestedFilename()).toMatch(/\.md$/);
  expect(readFileSync((await mdDl.path())!, "utf8")).toContain("# StackTriage report: spring\\_boot.log");

  // note deletion with confirmation
  await page.getByRole("button", { name: /Delete note/ }).first().click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await page.getByRole("dialog").getByRole("button", { name: "Delete note" }).click();
  await expect(page.getByRole("list", { name: "Notes" })).toHaveCount(0);

  expect(errors).toEqual([]);
});

test("(b) re-uploading the same file reports a duplicate and links to the existing import", async ({ page }) => {
  await page.goto("/import");
  await chooseFile(page, "spring_boot.log");
  await page.getByRole("button", { name: "Upload and analyse" }).click();
  await expect(page.getByRole("region", { name: "Ingestion summary" })).toBeVisible();
  await expect(page.getByText("Already imported", { exact: true })).toHaveCount(0);

  await chooseFile(page, "spring_boot.log");
  await page.getByRole("button", { name: "Upload and analyse" }).click();
  const dup = page.getByText("Already imported", { exact: true });
  await expect(dup).toBeVisible();
  await expect(page.getByText("so nothing new was added")).toBeVisible();
  const link = page.getByRole("link", { name: "Open it in Overview" });
  await expect(link).toBeVisible();
  await link.click();
  await expect(page).toHaveURL(/\/\?import=\d+/);
  await expect(page.getByRole("heading", { name: "Log volume over time" })).toBeVisible();

  // still exactly one import on the server
  await page.goto("/import");
  await expect(page.locator("[data-testid=imports-table] tbody tr")).toHaveCount(1);
});

test("(c) a file that is not a log shows the server's 415 message", async ({ page }) => {
  await page.goto("/import");
  const prose = "The quick brown fox jumps over the lazy dog.\nThis is a paragraph of ordinary prose and not a log file at all.\nNothing here looks like a timestamp or a level.\n".repeat(5);
  await chooseFile(page, { name: "notes.txt", mimeType: "text/plain", buffer: Buffer.from(prose) });
  await page.getByRole("button", { name: "Upload and analyse" }).click();
  const alert = page.getByRole("alert").filter({ hasText: /Import failed/ });
  await expect(alert).toBeVisible();
  await expect(alert).toContainText("415");
  await expect(alert).toContainText("not a supported log format");
  // nothing was imported
  await expect(page.getByRole("region", { name: "Ingestion summary" })).toHaveCount(0);
});

test("empty files are rejected on the client before upload", async ({ page }) => {
  await page.goto("/import");
  await chooseFile(page, { name: "empty.log", mimeType: "text/plain", buffer: Buffer.alloc(0) });
  await expect(page.getByText("This file is empty")).toBeVisible();
  await expect(page.getByRole("button", { name: "Upload and analyse" })).toBeDisabled();
});

test("server limits and bad options produce specific messages (413, 422)", async ({ page }) => {
  await page.goto("/import");
  // 422: an invalid service name is rejected by the server and its message is shown
  await chooseFile(page, "pino_payments.jsonl");
  await page.getByLabel("Service name (optional)").fill("bad$name!");
  await page.getByRole("button", { name: "Upload and analyse" }).click();
  const alert = page.getByRole("alert").filter({ hasText: /Import failed/ });
  await expect(alert).toContainText("422");
  await expect(alert).toContainText(/service/i);

  // client-side large file warning (does not block, server decides)
  const big = Buffer.alloc(26 * 1024 * 1024, "a");
  await chooseFile(page, { name: "huge.log", mimeType: "text/plain", buffer: big });
  await expect(page.getByText("Large file")).toBeVisible();
  await page.getByLabel("Service name (optional)").fill("");
  await page.getByRole("button", { name: "Upload and analyse" }).click();
  const tooBig = page.getByRole("alert").filter({ hasText: /Import failed/ });
  await expect(tooBig).toContainText("413");
  await expect(tooBig).toContainText("too large");
});

test("upload shows real progress, then a processing state", async ({ page, context }) => {
  await page.goto("/import");
  const cdp = await context.newCDPSession(page);
  await cdp.send("Network.enable");
  await cdp.send("Network.emulateNetworkConditions", { offline: false, latency: 20, downloadThroughput: -1, uploadThroughput: 1024 * 1024 });
  const line = '2026-03-05 10:00:00,000 INFO  [main] c.e.Service - heartbeat ok\n';
  const errorLine = "2026-03-05 10:00:30,000 ERROR [main] c.e.Service - upstream timed out\n";
  const content = Buffer.from(errorLine + line.repeat(Math.ceil((3 * 1024 * 1024) / line.length)));
  await chooseFile(page, { name: "big-heartbeat.log", mimeType: "text/plain", buffer: content });
  await page.getByRole("button", { name: "Upload and analyse" }).click();
  const progress = page.getByRole("progressbar", { name: "Upload progress" });
  await expect(progress).toBeVisible();
  await expect(page.getByText(/Uploading big-heartbeat\.log: \d+%/)).toBeVisible();
  await expect.poll(async () => Number(await progress.getAttribute("value")), { timeout: 20_000 }).toBeGreaterThan(5);
  await cdp.send("Network.emulateNetworkConditions", { offline: false, latency: 0, downloadThroughput: -1, uploadThroughput: -1 });
  await expect(page.getByRole("region", { name: "Ingestion summary" })).toBeVisible({ timeout: 40_000 });
});

test("dropping another file cannot replace an import while it is in progress", async ({ page }) => {
  const { promise: pending, resolve: release } = Promise.withResolvers<void>();
  await page.route("**/api/imports?*", async (route) => {
    if (route.request().method() !== "POST") return route.continue();
    await pending;
    await route.continue();
  });
  await page.goto("/import");
  await chooseFile(page, "spring_boot.log");
  await page.getByRole("button", { name: "Upload and analyse" }).click();
  await expect(page.getByRole("progressbar", { name: "Upload progress" })).toBeVisible();
  try {
    await dropFile(page, "replacement.log", readFileSync(fixture("node_express.log")));
    await expect(page.getByTestId("dropzone")).toContainText("spring_boot.log");
    await expect(page.getByRole("button", { name: "Importing" })).toBeDisabled();
    await expect(page.getByRole("button", { name: "Choose a different file" })).toBeDisabled();
  } finally {
    release();
  }
  await expect(page.getByRole("region", { name: "Ingestion summary" })).toContainText("spring_boot.log");
});

test("imports can be deleted after confirmation and the overview returns to its empty state", async ({ page, request }) => {
  await seed(request, "node_express.log");
  await page.goto("/import");
  const table = page.getByTestId("imports-table");
  await expect(table.getByText("node_express.log")).toBeVisible();
  await page.getByRole("button", { name: "Delete node_express.log" }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toContainText("node_express.log");
  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();
  await expect(table.getByText("node_express.log")).toBeVisible();
  await page.getByRole("button", { name: "Delete node_express.log" }).click();
  await dialog.getByRole("button", { name: "Delete import" }).click();
  await expect(page.getByText("No imports yet").first()).toBeVisible();
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Nothing imported yet" })).toBeVisible();
});

test("overview shows metrics for CSV telemetry and the unusual-period language is statistical", async ({ page, request }) => {
  const imp = await seed(request, "telemetry.csv");
  await page.goto(`/?import=${imp.id}`);
  await expect(page.getByRole("heading", { name: "Metrics" })).toBeVisible();
  await expect(page.getByRole("combobox", { name: "Metric" })).toContainText("cpu_pct");
  await expect(page.getByText(/not causes/).first()).toBeVisible();
  const ep = page.locator(".episode").first();
  await expect(ep).toContainText("Anomaly score");
  await expect(ep).toContainText("Type");
  await expect(ep).toContainText("Severity");
  await expect(ep.getByRole("table")).toContainText("Observed");
  await expect(page.locator("body")).not.toContainText(/root cause/i);
});

test("timestamps are UTC by default with a labelled toggle to local time; theme choice persists", async ({ browser, request }) => {
  await seed(request, "spring_boot.log");
  const ctx = await browser.newContext({ timezoneId: "America/New_York", colorScheme: "light", baseURL: "http://127.0.0.1:8765" });
  const page = await ctx.newPage();
  await page.goto("/");
  await expect(page.locator(".stat-strip")).toContainText("2026-03-05 10:00:26");
  await expect(page.locator(".stat-strip")).toContainText("UTC");
  await page.getByRole("button", { name: /Times shown in UTC/ }).click();
  await expect(page.locator(".stat-strip")).toContainText("2026-03-05 05:00:26");
  await expect(page.locator(".stat-strip")).toContainText(/EST|GMT-5/);
  await page.reload();
  await expect(page.locator(".stat-strip")).toContainText("2026-03-05 05:00:26");

  expect(await page.evaluate(() => document.documentElement.dataset.theme)).toBeUndefined();
  await page.getByRole("button", { name: "Dark theme" }).click();
  expect(await page.evaluate(() => document.documentElement.dataset.theme)).toBe("dark");
  await page.reload();
  expect(await page.evaluate(() => document.documentElement.dataset.theme)).toBe("dark");
  await expect(page.getByRole("button", { name: "Dark theme" })).toHaveAttribute("aria-pressed", "true");
  await ctx.close();
});

test("unknown incident and import ids show helpful not-found states", async ({ page, request }) => {
  const imp = await seed(request, "spring_boot.log");
  await page.goto(`/imports/${imp.id}/incidents/doesnotexist`);
  await expect(page.getByRole("heading", { name: "Incident not found" })).toBeVisible();
  await page.goto("/imports/99999/incidents");
  await expect(page.getByRole("heading", { name: "That import no longer exists" })).toBeVisible();
  await page.goto("/no/such/page");
  await expect(page.getByRole("heading", { name: "Page not found" })).toBeVisible();
});

test("a log with no errors imports cleanly and every view shows an empty-incidents state", async ({ page }) => {
  const info = "2026-03-05 10:00:00,000 INFO  [main] c.e.Service - heartbeat ok\n2026-03-05 10:01:00,000 INFO  [main] c.e.Service - heartbeat ok\n2026-03-05 10:02:00,000 INFO  [main] c.e.Service - heartbeat ok\n";
  await page.goto("/import");
  await chooseFile(page, { name: "quiet.log", mimeType: "text/plain", buffer: Buffer.from(info) });
  await page.getByRole("button", { name: "Upload and analyse" }).click();
  const summary = page.getByRole("region", { name: "Ingestion summary" });
  await expect(summary).toBeVisible();
  await expect(summary.locator(".stat", { hasText: "Error groups" })).toContainText("0");
  await summary.getByRole("link", { name: /View 0 incidents/ }).click();
  await expect(page.getByRole("heading", { name: "No error groups in this import" })).toBeVisible();
  await page.goto("/");
  await expect(page.getByText("No error groups in this import.")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Unusual periods" })).toBeVisible();
});

test("opening a related incident resets log record pagination", async ({ page, request }) => {
  const repeated = { timestamp: "2026-03-05T10:00:00Z", level: "error", service: "checkout", message: "repeated upstream failure" };
  const lines = [...Array.from({ length: 12 }, () => repeated), { ...repeated, message: "single database failure" }];
  const imp = await seed(request, {
    name: "related-records.jsonl",
    content: Buffer.from(lines.map((line) => JSON.stringify(line)).join("\n") + "\n"),
  });
  await page.goto(`/imports/${imp.id}/incidents`);
  await page.locator(".title-link").filter({ hasText: "repeated upstream failure" }).click();
  await expect(page.locator(".event")).toHaveCount(10);
  await page.getByRole("navigation", { name: "Records pagination" }).getByRole("button", { name: "Next" }).click();
  await expect(page.locator(".event")).toHaveCount(2);
  await page.locator(".related-title").filter({ hasText: "single database failure" }).click();
  await expect(page.getByRole("heading", { level: 1 })).toContainText("single database failure");
  await expect(page.locator(".event")).toHaveCount(1);
  await expect(page.locator(".event-msg")).toHaveText("single database failure");
});
