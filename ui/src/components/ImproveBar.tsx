import { useState } from "react";
import { api } from "../lib/api";

interface ImproveAccepted {
  run_id: string;
  status: string;
  corrections: number;
  message: string;
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
    delay = Math.min(5000, delay + 500);
  }
  throw new Error(
    `Improve didn't complete within ${maxSeconds}s — check the activity log / job status.`,
  );
}

// Phase-2 Improve bar. On the latest, still-pending version: takes the reviewer's
// open comments + overrides and asks the ImproverAgent (AgentCore) to produce a new
// version — overrides applied + derived recomputed deterministically, commented
// cells re-synthesized from source. Async: the API returns immediately, we poll for
// the new version to appear, then jump to it.
export function ImproveBar({
  union,
  period,
  approvalState,
  isLatest,
  version,
  onImproved,
}: {
  union: string;
  period: string;
  approvalState: string;
  isLatest: boolean;
  version?: number;
  onImproved: (toVersion: number) => void;
}): JSX.Element | null {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [ok, setOk] = useState("");

  // Only on the latest version of a sheet still in review — you improve the
  // version you're looking at, and only before it's approved/published.
  if (approvalState !== "pending_review" || !isLatest) return null;

  const local = (union.match(/(\d{2,4})\s*$/) || [])[1] || union;

  const run = async () => {
    setBusy(true);
    setError("");
    setOk("");
    try {
      const fromVersion = version ?? 1;
      const r = await api.post<ImproveAccepted>(
        `/v1/unions/${local}/rate-sheets/${period}/improve`,
        {},
      );
      setOk(r.message || "Improving…");
      const newVersion = await pollForNewVersion(local, period, fromVersion, 300);
      setOk(`Improved → v${newVersion}. Switched to it — review the highlighted changes.`);
      onImproved(newVersion);
    } catch (e) {
      // includes the 422 "no open corrections" message
      setError(String(e).replace(/^Error:\s*/, ""));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="rounded-lg border border-brand/30 bg-brand/5 shadow-sm">
      <div className="flex flex-wrap items-center gap-3 border-b border-brand/20 px-5 py-3">
        <span className="text-xs font-semibold uppercase tracking-wide text-brand">
          AI Improve
        </span>
        <span className="text-xs text-slate-600">
          Apply your comments + overrides — the agent recomputes derived columns and
          re-synthesizes commented cells into a new version for review.
        </span>
      </div>
      <div className="flex flex-wrap items-center gap-3 px-5 py-3">
        <button
          type="button"
          disabled={busy}
          onClick={run}
          className="rounded-md bg-brand px-4 py-1.5 text-sm font-semibold text-white shadow-sm transition hover:bg-brand-dark disabled:cursor-not-allowed disabled:bg-slate-300"
          title="Send this sheet's open corrections to the ImproverAgent (Bedrock + rate_math) → a new version."
        >
          {busy ? "Improving… (agent running)" : "✨ Improve with AI"}
        </button>
        <span className="text-xs text-slate-500">
          Creates a new version; this one stays unchanged. A human still approves.
        </span>
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
