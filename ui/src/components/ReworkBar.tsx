import { useState } from "react";
import { api } from "../lib/api";

type ReworkMode = "merge" | "ai";

interface ReworkResult {
  to_version: number;
  applied_overrides: number;
  comments_incorporated: number;
  mode: ReworkMode;
  agent_summary?: Record<string, unknown> | null;
}

interface ReworkAccepted {
  accepted: true;
  mode: "ai";
  eta_seconds: number;
}

interface RateSheetVersionsLite {
  versions: { version: number }[];
}

async function pollForNewVersion(
  local: string,
  period: string,
  fromVersion: number,
  maxSeconds: number,
): Promise<number> {
  const deadline = Date.now() + maxSeconds * 1000;
  let delay = 2500;
  while (Date.now() < deadline) {
    await new Promise((r) => setTimeout(r, delay));
    try {
      const r = await api.get<RateSheetVersionsLite>(
        `/v1/unions/${local}/rate-sheets/${period}`,
      );
      const latest = r.versions?.[0]?.version ?? fromVersion;
      if (latest > fromVersion) return latest;
    } catch {
      // transient — keep polling
    }
    // gentle backoff (2.5s → 5s) to ease load while the agent runs
    delay = Math.min(5000, delay + 500);
  }
  throw new Error(
    `AI rework didn't complete within ${maxSeconds}s — check the activity log.`,
  );
}

// Tier 3 — Rework action bar. Visible only on the latest version of a
// rejected sheet. Two modes:
//
//  • merge (default): the rework Lambda copies parent cells to v(N+1)
//    applying every override in DDB, patches the canonical CSV by
//    (Package, column) → new value, regenerates the xlsx. ~2-3s.
//
//  • ai: the rework Lambda synchronously invokes the ExtractorAgent on
//    AgentCore Runtime in direct mode, passing rework_context (rejection
//    reason + tags + applied overrides + cell comments) in the payload.
//    The agent re-extracts from the source PDF, returns a fresh CSV, and
//    the Lambda then applies overrides on top. ~30-60s.
//    For the 5 kernel unions the agent's output equals the merge output —
//    the path exists so future Path-C unions can re-prompt Claude with
//    rework_context.
export function ReworkBar({
  union,
  period,
  approvalState,
  version,
  parentVersion,
  onReworked,
}: {
  union: string;
  period: string;
  approvalState: string;
  version?: number;
  parentVersion?: number | null;
  onReworked: (toVersion: number) => void;
}): JSX.Element | null {
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState<ReworkMode | null>(null);
  const [error, setError] = useState("");
  const [ok, setOk] = useState("");

  // Only meaningful on the LATEST version (you can't rework an older snapshot)
  // and only when the sheet is in a rejected state.
  const isLatest = !parentVersion || version === undefined;
  if (approvalState !== "rejected" || !isLatest) return null;

  const local = (union.match(/(\d{2,4})\s*$/) || [])[1] || union;

  const submit = async (mode: ReworkMode) => {
    setBusy(mode);
    setError("");
    setOk("");
    try {
      if (mode === "ai") {
        // API Gateway has a 29s integration timeout; the agent takes ~60s.
        // Backend returns 202 immediately + dispatches the work async; we
        // poll the rate-sheet endpoint until a new version appears.
        const fromVersion = version ?? 1;
        await api.post<ReworkAccepted>(
          `/v1/unions/${local}/rate-sheets/${period}/rework`,
          { note, mode },
        );
        const newVersion = await pollForNewVersion(local, period, fromVersion, 180);
        setOk(
          `Reworked → v${newVersion} [ai]. AgentCore Runtime + overrides applied (took ~${Math.round(
            (Date.now() - (window as any).__t0 || 60_000) / 1000,
          )}s).`,
        );
        setNote("");
        onReworked(newVersion);
      } else {
        const r = await api.post<ReworkResult>(
          `/v1/unions/${local}/rate-sheets/${period}/rework`,
          { note, mode },
        );
        setOk(
          `Reworked → v${r.to_version} [${r.mode}]. ${r.applied_overrides} override(s) + ${r.comments_incorporated} comment(s) incorporated.`,
        );
        setNote("");
        onReworked(r.to_version);
      }
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="rounded-lg border border-amber-200 bg-amber-50/60 shadow-sm">
      <div className="flex flex-wrap items-center gap-3 border-b border-amber-200 px-5 py-3">
        <span className="text-xs font-medium uppercase tracking-wide text-amber-700">
          Rework
        </span>
        <span className="text-xs text-amber-700">
          This sheet was rejected. Apply your overrides + rejection feedback to
          create a new version.
        </span>
      </div>
      <div className="flex flex-wrap items-center gap-3 px-5 py-3">
        <input
          type="text"
          value={note}
          onChange={(e) => setNote(e.target.value)}
          placeholder="Optional note for the rework (visible in the activity log)"
          className="flex-1 rounded-md border border-amber-200 bg-white px-3 py-1.5 text-sm focus:border-amber-400 focus:outline-none focus:ring-1 focus:ring-amber-400"
          disabled={busy !== null}
        />
        <button
          type="button"
          disabled={busy !== null}
          onClick={() => submit("merge")}
          className="rounded-md bg-amber-600 px-4 py-1.5 text-sm font-medium text-white shadow-sm transition hover:bg-amber-700 disabled:cursor-not-allowed disabled:bg-slate-300"
          title="Deterministic merge: parent cells + your overrides → v(N+1). Fast (~2s)."
        >
          {busy === "merge" ? "Reworking…" : "Apply overrides → new version"}
        </button>
        <button
          type="button"
          disabled={busy !== null}
          onClick={() => submit("ai")}
          className="rounded-md bg-indigo-600 px-4 py-1.5 text-sm font-medium text-white shadow-sm transition hover:bg-indigo-700 disabled:cursor-not-allowed disabled:bg-slate-300"
          title="Re-invoke the ExtractorAgent on AgentCore Runtime with your rejection feedback + comments in the payload, then apply overrides. Slower (~30-60s) but uses the AI path end-to-end."
        >
          {busy === "ai" ? "AI re-extracting (≈30-60s)…" : "✨ Re-extract with AI feedback"}
        </button>
      </div>
      {(error || ok) && (
        <div
          className={`px-5 py-2 text-xs ${
            error ? "bg-rose-50 text-rose-700" : "bg-emerald-50 text-emerald-700"
          }`}
        >
          {error || ok}
        </div>
      )}
    </div>
  );
}
