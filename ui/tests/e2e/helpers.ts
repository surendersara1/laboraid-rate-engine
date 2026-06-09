import { spawnSync } from "node:child_process";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import type { Page } from "@playwright/test";

// __dirname shim for ESM.
const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

const RESET_SCRIPT = resolve(__dirname, "../../../_TMP_/reset_for_demo.py");

/**
 * Reset 704/2026-01-01 back to a single-version pending_review row + clear
 * the per-period audit_log rows + DDB overrides. Used by spec files that
 * mutate state so each one starts deterministic.
 */
export function resetDemoState(): void {
  const r = spawnSync(process.env.E2E_PYTHON ?? "python", [RESET_SCRIPT], {
    env: { ...process.env, AWS_PROFILE: process.env.AWS_PROFILE ?? "laboraid" },
    encoding: "utf-8",
  });
  if (r.status !== 0) {
    console.error("[e2e] reset_for_demo.py failed");
    console.error(r.stdout);
    console.error(r.stderr);
    throw new Error(`reset_for_demo.py exited ${r.status}`);
  }
  // Surface the script's "Final state:" line so the test output shows what we
  // reset to.
  const finalLine = (r.stdout.split("\n").find((l) => /v\d+\s+state=/.test(l)) || "")
    .trim();
  if (finalLine) console.log(`[e2e] reset → ${finalLine}`);
}

export function escapeRegex(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/**
 * Open the Sprinkler 704 / 2026-01-01 rate sheet and wait for the header.
 * The auth fixture already signed us in.
 */
export async function openRateSheet704(
  page: Page,
  qs = "",
): Promise<void> {
  const url = `/business/rate-sheets/Sprinkler+704/2026-01-01${qs}`;
  await page.goto(url);
  await page.waitForSelector("h2", { state: "visible" });
}
