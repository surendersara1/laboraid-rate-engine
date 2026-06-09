import { expect, test } from "@playwright/test";

import { escapeRegex, openRateSheet704, resetDemoState } from "./helpers";

// Tier 1 — non-destructive read-only checks of the Business review surface.
// Header card, four artifact cards, provenance panel, cell table, activity
// panel (empty state). Runs first; we reset state before the suite so the
// page is in a known v1 / pending_review shape.

test.describe.serial("Tier 1 — Business review (read-only)", () => {
  test.beforeAll(() => resetDemoState());

  test.beforeEach(async ({ page }) => {
    await openRateSheet704(page);
  });

  test("header shows union + period + counts + pending review pill", async ({
    page,
  }) => {
    await expect(page.getByRole("heading", { name: /Sprinkler[ +]704/ })).toBeVisible();
    await expect(page.getByText("pending review")).toBeVisible();
    // The kernel produces 13 classifications, 221 cells, 1 honest gap for 704.
    await expect(page.getByText(/13\s+classifications/)).toBeVisible();
    await expect(page.getByText(/221\s+cells/)).toBeVisible();
    await expect(page.getByText(/1\s+gap/)).toBeVisible();
    await page.screenshot({
      path: "test-results/tier1-header.png",
      fullPage: true,
    });
  });

  test("all four artifact cards render (PDF, CSV, xlsx, gap JSON)", async ({
    page,
  }) => {
    for (const name of [
      "Source PDF",
      "Canonical CSV",
      "Excel (xlsx)",
      "Gap report (JSON)",
    ]) {
      // escapeRegex so the parens/brackets aren't parsed as regex groups.
      await expect(
        page.getByText(new RegExp(escapeRegex(name))).first(),
      ).toBeVisible();
    }
    // The "Open ↗" affordance on each card means a presigned URL came back.
    // For 704 today, all four artifacts are produced.
    const opens = page.getByText(/^Open ↗$/);
    expect(await opens.count()).toBeGreaterThanOrEqual(4);
    await page.screenshot({
      path: "test-results/tier1-artifacts.png",
      fullPage: true,
    });
  });

  test("selecting a cell row populates the provenance panel", async ({
    page,
  }) => {
    // Click the Journeyman · Wage row — provenance panel should swap from
    // the empty-state copy to the actual cell details.
    await expect(page.getByText(/Click a row in the table/)).toBeVisible();
    const row = page
      .locator("tr")
      .filter({ has: page.getByText(/^Journeyman$/) })
      .filter({ has: page.getByText(/^Wage$/) })
      .first();
    await row.scrollIntoViewIfNeeded();
    await row.click();
    await expect(page.getByText("Selected cell")).toBeVisible();
    await expect(page.getByText(/Provenance/).first()).toBeVisible();
    // Confidence pill + the comment/override buttons appear on selection.
    await expect(page.getByRole("button", { name: /💬 Comment/ })).toBeVisible();
    await expect(page.getByRole("button", { name: /✎ Override/ })).toBeVisible();
    await page.screenshot({
      path: "test-results/tier1-provenance.png",
      fullPage: true,
    });
  });

  test("activity panel shows empty state when nothing has happened", async ({
    page,
  }) => {
    await expect(
      page.getByText(/Nothing yet.*Approve.*reject.*comment.*override/),
    ).toBeVisible();
    await page.screenshot({
      path: "test-results/tier1-activity-empty.png",
      fullPage: true,
    });
  });

  test("v1 baseline: no version dropdown + no rework bar", async ({ page }) => {
    await expect(page.getByText(/v1 · current/)).toBeVisible();
    expect(await page.locator("select").count()).toBe(0);
    await expect(
      page.getByText(/Apply overrides → new version/),
    ).toHaveCount(0);
  });
});
