import { useState } from "react";
import { api } from "../lib/api";

// Tier 3 — Rework action bar. Visible only when the rate sheet is rejected
// AND there are pending overrides + a rejection reason; the backend collects
// both from DDB/Aurora when it builds the new version, so the UI doesn't need
// to send them inline. We just give the reviewer a single "create v(N+1)"
// button + an optional note.
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
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [ok, setOk] = useState("");

  // Only meaningful on the LATEST version (you can't rework an older snapshot)
  // and only when the sheet is in a rejected state.
  const isLatest = !parentVersion || version === undefined;
  if (approvalState !== "rejected" || !isLatest) return null;

  const local = (union.match(/(\d{2,4})\s*$/) || [])[1] || union;

  const submit = async () => {
    setBusy(true);
    setError("");
    setOk("");
    try {
      const r = await api.post<{ to_version: number; applied_overrides: number }>(
        `/v1/unions/${local}/rate-sheets/${period}/rework`,
        { note },
      );
      setOk(
        `Reworked → v${r.to_version}. ${r.applied_overrides} override(s) applied.`,
      );
      setNote("");
      onReworked(r.to_version);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
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
          disabled={busy}
        />
        <button
          type="button"
          disabled={busy}
          onClick={submit}
          className="rounded-md bg-amber-600 px-4 py-1.5 text-sm font-medium text-white shadow-sm transition hover:bg-amber-700 disabled:cursor-not-allowed disabled:bg-slate-300"
        >
          {busy ? "Reworking…" : "Apply overrides → new version"}
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
