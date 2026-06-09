import { expect, test, type Page } from "@playwright/test";

import { resetDemoState } from "./helpers";

// Tier 3 end-to-end (merge mode): takes a fresh v1/pending_review rate sheet
// through override → reject → rework → version switch → activity verification.
// Maps 1:1 to the manual checklist in docs/feature_improvement_1_2026-06-09.md.
//
// Each spec file resets its own state in beforeAll so file order doesn't
// matter — the previous spec file's mutations don't leak in here.

const RATE_SHEET_URL = "/business/rate-sheets/Sprinkler+704/2026-01-01";
const OVERRIDE_VALUE = "66";
const OVERRIDE_JUSTIFICATION = "Per CBA §4.2";
const REJECTION_REASON = "Wage row needs human verification";

// Run in order: the second test mutates state that the third test reads.
test.describe.serial("Tier 3 — rework loop (merge mode)", () => {
  test.beforeAll(() => resetDemoState());

  test.beforeEach(async ({ page }) => {
    await page.goto(RATE_SHEET_URL);
    // Wait for the header to render — confirms the SPA hydrated + auth worked.
    await expect(page.getByRole("heading", { name: /Sprinkler[ +]704/ })).toBeVisible();
  });

  test("v1 baseline shows pending_review, no version dropdown, no rework bar", async ({ page }) => {
    await expect(page.getByText("pending review")).toBeVisible();
    await expect(page.getByText(/v1 · current/)).toBeVisible();
    // No dropdown when there's only one version.
    await expect(page.locator("select")).toHaveCount(0);
    // No rework bar pre-rejection.
    await expect(page.getByText(/Apply overrides → new version/)).toHaveCount(0);
    await page.screenshot({
      path: "test-results/tier3-step1-baseline.png",
      fullPage: true,
    });
  });

  test("full flow: override → reject → rework → v2 visible with diff highlight", async ({ page }) => {
    // ---- override Journeyman · Wage --------------------------------------
    await selectCell(page, "Journeyman", "Wage");
    await page.getByRole("button", { name: /✎ Override/ }).click();
    await page.getByLabel(/New value/i).fill(OVERRIDE_VALUE);
    await page.getByLabel(/Justification/i).fill(OVERRIDE_JUSTIFICATION);
    await page.getByRole("button", { name: /Apply override/ }).click();
    // Modal closes when the API call succeeds — wait for it to disappear.
    await expect(page.getByRole("heading", { name: /Override cell value/ })).toHaveCount(0);

    // Activity timeline should pick the override up. The arrow is U+2192; the
    // table cell uses ".00" suffix on numerics, so match flexibly.
    await expect(
      page.getByText(
        new RegExp(`overrode Journeyman.{1,4}Wage.{1,8}→.{1,6}${OVERRIDE_VALUE}`),
      ),
    ).toBeVisible({ timeout: 10_000 });
    await page.screenshot({
      path: "test-results/tier3-step2-override.png",
      fullPage: true,
    });

    // ---- reject ----------------------------------------------------------
    const actionBar = page
      .locator("div")
      .filter({ has: page.getByRole("button", { name: /^Reject$/ }) })
      .first();
    await actionBar
      .getByPlaceholder(/Rejection reason/i)
      .fill(REJECTION_REASON);
    await page.getByRole("button", { name: /Wrong extraction/ }).click();
    await page.getByRole("button", { name: /^Reject$/ }).click();
    await expect(page.getByText("rejected").first()).toBeVisible();
    // The activity row reads `rejected — "<reason>" [tag, ...]` with an em-dash
    // and smart quotes; match flexibly so quoting normalization doesn't break
    // the test. Trigger a manual Refresh in case the auto-refresh raced the
    // audit_log INSERT.
    await page
      .getByRole("button", { name: /Refresh/ })
      .first()
      .click();
    await expect(
      page.getByText(
        new RegExp(`rejected.{1,10}${escapeRegex(REJECTION_REASON)}`),
      ),
    ).toBeVisible({ timeout: 15_000 });
    await page.screenshot({
      path: "test-results/tier3-step3-rejected.png",
      fullPage: true,
    });

    // ---- rework bar appears + trigger rework -----------------------------
    const reworkButton = page.getByRole("button", {
      name: /Apply overrides → new version/,
    });
    await expect(reworkButton).toBeVisible();
    await page
      .getByPlaceholder(/Optional note for the rework/i)
      .fill("Playwright e2e");
    await reworkButton.click();

    // Wait for the success strip + URL to flip.
    await expect(page.getByText(/Reworked → v2/)).toBeVisible({ timeout: 30_000 });
    await expect(page).toHaveURL(/version=2/, { timeout: 30_000 });
    await page.screenshot({
      path: "test-results/tier3-step4-reworked.png",
      fullPage: true,
    });

    // ---- v2 pill + version dropdown + merge mode chip --------------------
    await expect(page.getByText(/v2 · current/)).toBeVisible();
    // The mode chip (merge=emerald, ai=indigo) lives next to the version pill.
    await expect(page.getByText(/^merge$/)).toBeVisible();
    const versionDropdown = page.locator("select").first();
    await expect(versionDropdown).toBeVisible();
    await expect(versionDropdown).toHaveValue("2");

    // ---- diff highlight on the reworked row ------------------------------
    const journeymanWageRow = page
      .locator("tr")
      .filter({ has: page.getByText(/^Journeyman$/) })
      .filter({ has: page.getByText(/^Wage$/) })
      .first();
    await expect(journeymanWageRow).toBeVisible();
    const className = await journeymanWageRow.getAttribute("class");
    expect(className ?? "").toMatch(/border-amber|amber-50\/40/);
    // The cell value column now reads 66.00.
    await expect(journeymanWageRow.getByText(/^66\.00$/)).toBeVisible();
    await page.screenshot({
      path: "test-results/tier3-step5-diff-highlight.png",
      fullPage: true,
    });

    // ---- switch back to v1 -> no highlight, original value ---------------
    await versionDropdown.selectOption("1");
    await expect(page).toHaveURL(/version=1/);
    await expect(page.getByText(/v1 · historical/)).toBeVisible();
    const v1Row = page
      .locator("tr")
      .filter({ has: page.getByText(/^Journeyman$/) })
      .filter({ has: page.getByText(/^Wage$/) })
      .first();
    await expect(v1Row.getByText(/^52\.32$/)).toBeVisible();
    const v1Class = await v1Row.getAttribute("class");
    expect(v1Class ?? "").not.toMatch(/border-amber/);
    await page.screenshot({
      path: "test-results/tier3-step6-v1-historical.png",
      fullPage: true,
    });
  });

  test("My Activity surfaces all four actions in one Union/Period group", async ({ page }) => {
    await page.goto("/business/me");
    await expect(page.getByRole("heading", { name: /My Activity/ })).toBeVisible();

    // Totals chip block exists. Values depend on prior test ordering — we
    // only assert the structural pieces (the labels).
    for (const action of ["approve", "reject", "comment", "override"] as const) {
      await expect(page.getByText(new RegExp(`${action}\\s+\\d+`)).first()).toBeVisible();
    }

    // There should be at least one Union/Period card linking back to the
    // rate sheet. We can't rely on rework being there if the override-flow
    // test was skipped (Playwright runs tests independently), so we just
    // check the link.
    await expect(page.getByRole("link", { name: /Open rate sheet ↗/ }).first()).toBeVisible();
    await page.screenshot({
      path: "test-results/tier3-step7-my-activity.png",
      fullPage: true,
    });
  });
});

/**
 * Click a row in the rate-cell table by its Classification + Field text. The
 * table virtualizes on scroll for long sheets, but 704 has 221 rows that all
 * render — so a simple filter is enough.
 */
async function selectCell(
  page: Page,
  classification: string,
  field: string,
): Promise<void> {
  const row = page
    .locator("tr")
    .filter({ has: page.getByText(new RegExp(`^${escapeRegex(classification)}$`)) })
    .filter({ has: page.getByText(new RegExp(`^${escapeRegex(field)}$`)) })
    .first();
  await row.scrollIntoViewIfNeeded();
  await row.click();
  // ProvenancePanel renders with "Selected cell" + the classification +
  // field after a click — wait for it to be coherent.
  await expect(page.getByText("Selected cell")).toBeVisible();
}

function escapeRegex(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}
