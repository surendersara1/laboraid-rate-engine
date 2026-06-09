import { expect, test, type Page } from "@playwright/test";

import { escapeRegex, openRateSheet704, resetDemoState } from "./helpers";

// Tier 2 — comment / override / reject / approve, then verify the per-sheet
// Activity timeline AND the global My Activity page picked them up. State
// mutates within the file; describe.serial keeps the order stable.

const COMMENT_TEXT = "T2 e2e comment — verify per-letter";
const OVERRIDE_VALUE = "63.5";
const OVERRIDE_JUSTIFICATION = "T2 e2e — per CBA addendum";
const REJECT_REASON = "T2 e2e — needs human verification";

async function selectJourneymanWage(page: Page): Promise<void> {
  const row = page
    .locator("tr")
    .filter({ has: page.getByText(/^Journeyman$/) })
    .filter({ has: page.getByText(/^Wage$/) })
    .first();
  await row.scrollIntoViewIfNeeded();
  await row.click();
  await expect(page.getByText("Selected cell")).toBeVisible();
}

test.describe.serial("Tier 2 — review actions + activity log", () => {
  test.beforeAll(() => resetDemoState());

  test.beforeEach(async ({ page }) => {
    await openRateSheet704(page);
  });

  test("cell comment lands in activity timeline with actor email", async ({
    page,
  }) => {
    await selectJourneymanWage(page);
    await page.getByRole("button", { name: /💬 Comment/ }).click();
    await page.getByRole("textbox").last().fill(COMMENT_TEXT);
    await page.getByRole("button", { name: /Save comment/ }).click();
    await expect(
      page.getByRole("heading", { name: /Comment on cell/ }),
    ).toHaveCount(0);

    // Refresh activity (auto-refresh can race the audit-log INSERT).
    await page.getByRole("button", { name: /Refresh/ }).first().click();
    await expect(
      page.getByText(
        new RegExp(`commented.{1,5}${escapeRegex(COMMENT_TEXT)}`),
      ),
    ).toBeVisible({ timeout: 15_000 });
    // Actor should be the test user's email, NOT a UUID.
    await expect(
      page.getByText(/e2e@laboraid\.test/).first(),
    ).toBeVisible();
    await page.screenshot({
      path: "test-results/tier2-comment.png",
      fullPage: true,
    });
  });

  test("cell override records old → new + actor + justification", async ({
    page,
  }) => {
    await selectJourneymanWage(page);
    await page.getByRole("button", { name: /✎ Override/ }).click();
    await page.getByLabel(/New value/i).fill(OVERRIDE_VALUE);
    await page.getByLabel(/Justification/i).fill(OVERRIDE_JUSTIFICATION);
    await page.getByRole("button", { name: /Apply override/ }).click();
    await expect(
      page.getByRole("heading", { name: /Override cell value/ }),
    ).toHaveCount(0);

    await page.getByRole("button", { name: /Refresh/ }).first().click();
    await expect(
      page.getByText(
        new RegExp(
          `overrode Journeyman.{1,4}Wage.{1,8}→.{1,8}${escapeRegex(OVERRIDE_VALUE)}`,
        ),
      ),
    ).toBeVisible({ timeout: 15_000 });
    await page.screenshot({
      path: "test-results/tier2-override.png",
      fullPage: true,
    });
  });

  test("reject with tags + reason updates state pill and timeline", async ({
    page,
  }) => {
    const actionBar = page
      .locator("div")
      .filter({ has: page.getByRole("button", { name: /^Reject$/ }) })
      .first();
    await actionBar.getByPlaceholder(/Rejection reason/i).fill(REJECT_REASON);
    await page.getByRole("button", { name: /Wrong extraction/ }).click();
    await page.getByRole("button", { name: /CBA mismatch/ }).click();
    await page.getByRole("button", { name: /^Reject$/ }).click();

    await expect(page.getByText("rejected").first()).toBeVisible();
    await page.getByRole("button", { name: /Refresh/ }).first().click();
    await expect(
      page.getByText(
        new RegExp(`rejected.{1,10}${escapeRegex(REJECT_REASON)}`),
      ),
    ).toBeVisible({ timeout: 15_000 });
    // Tags should be shown in the same row.
    await expect(page.getByText(/wrong_extraction/)).toBeVisible();
    await expect(page.getByText(/cba_mismatch/)).toBeVisible();
    await page.screenshot({
      path: "test-results/tier2-rejected.png",
      fullPage: true,
    });
  });

  test("My Activity surfaces the actions grouped by union/period", async ({
    page,
  }) => {
    await page.goto("/business/me");
    await expect(page.getByRole("heading", { name: /My Activity/ })).toBeVisible();
    // Scope-me subtitle (we wired ?scope=me in the UI).
    await expect(page.getByText(/Your approvals/i)).toBeVisible();

    // Totals chips for the three actions taken above.
    await expect(page.getByText(/reject\s+1/)).toBeVisible();
    await expect(page.getByText(/comment\s+1/)).toBeVisible();
    await expect(page.getByText(/override\s+1/)).toBeVisible();

    // Card per {local, period} with a deep-link back.
    await expect(page.getByText(/Union 704 · 2026-01-01/)).toBeVisible();
    await expect(
      page.getByRole("link", { name: /Open rate sheet ↗/ }).first(),
    ).toBeVisible();

    // Filter pills narrow the list.
    await page.getByRole("button", { name: /^reject$/ }).click();
    await expect(
      page.getByText(new RegExp(escapeRegex(REJECT_REASON))).first(),
    ).toBeVisible();
    // Comment row should be hidden under the reject filter.
    await expect(
      page.getByText(new RegExp(escapeRegex(COMMENT_TEXT))),
    ).toHaveCount(0);
    await page.screenshot({
      path: "test-results/tier2-my-activity.png",
      fullPage: true,
    });
  });
});
