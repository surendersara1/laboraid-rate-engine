import { spawnSync } from "node:child_process";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { chromium, type FullConfig } from "@playwright/test";

import { amplifyStorageEntries, authenticate } from "./auth";

// __dirname shim for ESM (the ui workspace sets "type": "module").
const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

// Minimal .env loader so the suite picks up credentials written by
// _TMP_/create_e2e_user.py without pulling in dotenv.
function loadDotenv(path: string): void {
  if (!existsSync(path)) return;
  for (const line of readFileSync(path, "utf-8").split(/\r?\n/)) {
    const m = line.match(/^\s*([A-Z_][A-Z0-9_]*)\s*=\s*(.*?)\s*$/);
    if (!m) continue;
    if (process.env[m[1]] === undefined) process.env[m[1]] = m[2];
  }
}
loadDotenv(resolve(__dirname, ".auth/.env"));

// Runs once before the entire test suite. Two responsibilities:
//
//   1. Reset the dev demo data (704/2026-01-01 -> v1 pending_review, no audit
//      rows) by invoking _TMP_/reset_for_demo.py. Same script the human-driven
//      tests use, so the spec starts from a known state.
//
//   2. Authenticate demo@laboraid.test against the test-only Cognito client
//      and persist Amplify-compatible localStorage in tests/e2e/.auth/user.json
//      so individual specs inherit the session.
//
// E2E_PASSWORD must be set (do NOT commit it). Defaults to the documented
// demo password for local convenience; CI should override.
const BASE_URL = process.env.E2E_BASE_URL ?? "https://d3ggwschjt81wu.cloudfront.net";
const E2E_USERNAME = process.env.E2E_USERNAME ?? "e2e@laboraid.test";
const E2E_PASSWORD = process.env.E2E_PASSWORD;
const AWS_PROFILE = process.env.AWS_PROFILE ?? "laboraid";
const PYTHON = process.env.E2E_PYTHON ?? "python";

if (!E2E_PASSWORD) {
  throw new Error(
    "E2E_PASSWORD is not set. Run _TMP_/create_e2e_user.py first (writes " +
      "ui/tests/e2e/.auth/.env), or set the env var manually.",
  );
}

const STORAGE_STATE = resolve(__dirname, ".auth/user.json");
const RESET_SCRIPT = resolve(
  __dirname,
  "../../../_TMP_/reset_for_demo.py",
);

export default async function globalSetup(_config: FullConfig): Promise<void> {
  // ---- 1. Reset demo state ------------------------------------------------
  console.log("[e2e] resetting 704/2026-01-01 demo state…");
  const reset = spawnSync(PYTHON, [RESET_SCRIPT], {
    env: { ...process.env, AWS_PROFILE },
    encoding: "utf-8",
  });
  if (reset.status !== 0) {
    console.error(reset.stdout);
    console.error(reset.stderr);
    throw new Error(`reset_for_demo.py exited ${reset.status}`);
  }
  console.log(reset.stdout.trim());

  // ---- 2. Authenticate + build storage state ------------------------------
  console.log(`[e2e] authenticating ${E2E_USERNAME} via Cognito test client…`);
  const tokens = await authenticate(E2E_USERNAME, E2E_PASSWORD);

  // Boot a throwaway browser, navigate to the SPA so localStorage exists for
  // this origin, write the Amplify entries, then dump storage state.
  const browser = await chromium.launch();
  const ctx = await browser.newContext();
  const page = await ctx.newPage();
  await page.goto(BASE_URL, { waitUntil: "domcontentloaded" });
  await page.evaluate((entries) => {
    for (const e of entries) window.localStorage.setItem(e.name, e.value);
  }, amplifyStorageEntries(tokens));
  mkdirSync(dirname(STORAGE_STATE), { recursive: true });
  await ctx.storageState({ path: STORAGE_STATE });
  await browser.close();

  // Also write a quick sentinel so we can detect stale auth in tests.
  writeFileSync(
    resolve(__dirname, ".auth/user.meta.json"),
    JSON.stringify({ username: E2E_USERNAME, baseUrl: BASE_URL, ts: new Date(0).toISOString() }, null, 2),
  );
  console.log("[e2e] storage state written");
}
