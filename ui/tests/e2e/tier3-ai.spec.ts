import { expect, test } from "@playwright/test";

import { escapeRegex, openRateSheet704, resetDemoState } from "./helpers";

// Tier 3.D — AI rework path. Same setup as merge mode (override + reject)
// but clicks the second ReworkBar button which routes through AgentCore
// Runtime. The agent call takes ~30-60s synchronously, so the spec
// timeout is lifted accordingly.

const COMMENT_TEXT = "T3.AI — bake in 8/1 letter";
const OVERRIDE_VALUE = "66";
const OVERRIDE_JUSTIFICATION = "T3.AI per CBA §4.2";
const REJECT_REASON = "T3.AI — re-extract via agent";

test.describe.serial("Tier 3 — rework via AI (AgentCore Runtime)", () => {
  // beforeEach (not beforeAll) so retries re-reset state too. Each retry
  // starts from a clean v1/pending_review row + cleared DDB overrides.
  test.beforeEach(() => resetDemoState());

  // AgentCore Runtime invokes for 704 land in ~45-90s. Lift the per-test
  // timeout and keep the assertion timeouts modest.
  test.setTimeout(240_000);
  // AgentCore can return transient 503s on cold-start contention; retry
  // once locally, twice on CI. The Lambda + agent logic itself is
  // deterministic — we just need a fresh slot.
  test.describe.configure({ retries: process.env.CI ? 2 : 1 });

  test("override + reject + AI rework → v2 with ai mode + agent_summary", async ({
    page,
  }) => {
    await openRateSheet704(page);
    // 1. Comment + override + reject on v1.
    const row = page
      .locator("tr")
      .filter({ has: page.getByText(/^Journeyman$/) })
      .filter({ has: page.getByText(/^Wage$/) })
      .first();
    await row.scrollIntoViewIfNeeded();
    await row.click();
    await page.getByRole("button", { name: /💬 Comment/ }).click();
    await page.getByRole("textbox").last().fill(COMMENT_TEXT);
    await page.getByRole("button", { name: /Save comment/ }).click();
    await expect(
      page.getByRole("heading", { name: /Comment on cell/ }),
    ).toHaveCount(0);

    await row.click();
    await page.getByRole("button", { name: /✎ Override/ }).click();
    await page.getByLabel(/New value/i).fill(OVERRIDE_VALUE);
    await page.getByLabel(/Justification/i).fill(OVERRIDE_JUSTIFICATION);
    await page.getByRole("button", { name: /Apply override/ }).click();
    await expect(
      page.getByRole("heading", { name: /Override cell value/ }),
    ).toHaveCount(0);

    await page
      .getByPlaceholder(/Rejection reason/i)
      .fill(REJECT_REASON);
    await page.getByRole("button", { name: /Wrong extraction/ }).click();
    await page.getByRole("button", { name: /^Reject$/ }).click();
    await expect(page.getByText("rejected").first()).toBeVisible();
    await page.screenshot({
      path: "test-results/tier3-ai-step1-rejected.png",
      fullPage: true,
    });

    // 2. Two ReworkBar buttons should be visible — assert and click the AI one.
    await expect(
      page.getByRole("button", { name: /Apply overrides → new version/ }),
    ).toBeVisible();
    const aiButton = page.getByRole("button", {
      name: /Re-extract with AI feedback/,
    });
    await expect(aiButton).toBeVisible();
    await page
      .getByPlaceholder(/Optional note for the rework/i)
      .fill("Playwright AI e2e");
    await aiButton.click();

    // The button label flips to "AI re-extracting" while the call runs.
    await expect(
      page.getByRole("button", { name: /AI re-extracting/ }),
    ).toBeVisible({ timeout: 5_000 });
    await page.screenshot({
      path: "test-results/tier3-ai-step2-spinner.png",
      fullPage: true,
    });

    // 3. UI polls in the background. When v2 appears the ReworkBar unmounts
    //    (state becomes pending_review on the new version), so we can't assert
    //    on the in-bar success line — it flashes and disappears. Instead wait
    //    for the URL to flip + the v2 mode chip to appear. ~45-90s for the
    //    agent; allow 180s.
    await expect(page).toHaveURL(/version=2/, { timeout: 180_000 });
    await expect(page.getByText(/v2 · current/)).toBeVisible({ timeout: 15_000 });
    await page.screenshot({
      path: "test-results/tier3-ai-step3-reworked.png",
      fullPage: true,
    });

    // 4. The mode chip on the header is indigo "✨ ai".
    await expect(page.getByText(/^✨ ai$/)).toBeVisible();
    await expect(page.getByText(/v2 · current/)).toBeVisible();

    // 5. Activity row carries action='rework'. The audit_log INSERT happens
    //    AFTER the v2 rate_periods INSERT (cells + xlsx render are in between,
    //    ~20-30s), so the row may not be visible immediately when the URL
    //    flips. Click Refresh in a polling loop until the row appears or 60s.
    const reworkRow = page.getByText(/^rework$/).first();
    const deadline = Date.now() + 60_000;
    while (Date.now() < deadline) {
      await page.getByRole("button", { name: /Refresh/ }).first().click();
      try {
        await reworkRow.waitFor({ state: "visible", timeout: 5_000 });
        break;
      } catch {
        // not yet — give the Lambda another beat to commit the audit row
        await page.waitForTimeout(2_000);
      }
    }
    await expect(reworkRow).toBeVisible();
    await page.screenshot({
      path: "test-results/tier3-ai-step4-activity.png",
      fullPage: true,
    });

    // 6. Switching back to v1 should still show the historical chip — and NOT
    //    show the ai chip (v1 is the original, no rework_context).
    const versionDropdown = page.locator("select").first();
    await versionDropdown.selectOption("1");
    await expect(page.getByText(/v1 · historical/)).toBeVisible();
    await expect(page.getByText(/^✨ ai$/)).toHaveCount(0);
    await page.screenshot({
      path: "test-results/tier3-ai-step5-v1-historical.png",
      fullPage: true,
    });

    // 7. Switch back to v2 and confirm the diff highlight is still on the
    //    Journeyman·Wage row (override applied on top of agent's CSV).
    //    selectOption() is sync DOM but the re-fetch + re-render is async; wait
    //    for the dropdown value + v2 chip + override value to land before
    //    sniffing class. The URL drops `?version=` when on the latest version
    //    (it's the default), so we can't assert that.
    await versionDropdown.selectOption("2");
    await expect(versionDropdown).toHaveValue("2");
    await expect(page.getByText(/v2 · current/)).toBeVisible();
    const wageRow = page
      .locator("tr")
      .filter({ has: page.getByText(/^Journeyman$/) })
      .filter({ has: page.getByText(/^Wage$/) })
      .first();
    await expect(
      wageRow.getByText(new RegExp(`^${escapeRegex(OVERRIDE_VALUE)}\\.00$`)),
    ).toBeVisible({ timeout: 10_000 });
    const cls = await wageRow.getAttribute("class");
    expect(cls ?? "").toMatch(/border-amber|amber-50\/40/);
    await page.screenshot({
      path: "test-results/tier3-ai-step6-v2-diff.png",
      fullPage: true,
    });
  });
});
