import { useState } from "react";
import { api } from "../lib/api";

const REJECTION_TAGS: Array<[string, string]> = [
  ["missing_data", "Missing data"],
  ["wrong_extraction", "Wrong extraction"],
  ["cba_mismatch", "CBA mismatch"],
  ["other", "Other"],
];

function unionLocal(display: string): string {
  const m = display.match(/(\d{2,4})\s*$/);
  return m ? m[1] : display;
}

// Top action bar: Approve / Reject (Tier 2.1+2.2).
// Approve disabled when reviewQueueEmpty=false or the sheet is already in a
// terminal state. Reject requires a reason; tags optionally narrow why.
export function ApproveRejectBar({
  union,
  period,
  approvalState,
  reviewQueueEmpty,
  onChanged,
}: {
  union: string;
  period: string;
  approvalState: string;
  reviewQueueEmpty: boolean;
  onChanged: (state: string) => void;
}): JSX.Element {
  const [reason, setReason] = useState("");
  const [tags, setTags] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [ok, setOk] = useState("");

  const local = unionLocal(union);
  const base = `/v1/unions/${local}/rate-sheets/${period}`;
  const isTerminal = approvalState === "approved" || approvalState === "published";

  const approve = async () => {
    setBusy(true);
    setError("");
    setOk("");
    try {
      await api.post(`${base}/approve`, {
        approval_state: approvalState,
        review_queue_empty: reviewQueueEmpty,
      });
      onChanged("approved");
      setOk("Approved.");
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  const reject = async () => {
    if (!reason.trim()) {
      setError("Rejection reason is required.");
      return;
    }
    setBusy(true);
    setError("");
    setOk("");
    try {
      await api.post(`${base}/reject`, {
        approval_state: approvalState,
        reason,
        tags,
      });
      onChanged("rejected");
      setOk("Rejected.");
      setReason("");
      setTags([]);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  const toggleTag = (t: string) => {
    setTags((cur) => (cur.includes(t) ? cur.filter((x) => x !== t) : [...cur, t]));
  };

  return (
    <div className="rounded-lg border border-slate-200 bg-white shadow-sm">
      <div className="flex flex-wrap items-center gap-3 border-b border-slate-100 px-5 py-3">
        <span className="text-xs font-medium uppercase tracking-wide text-slate-500">
          Action
        </span>
        <button
          type="button"
          disabled={busy || isTerminal || !reviewQueueEmpty}
          onClick={approve}
          className="rounded-md bg-emerald-600 px-4 py-1.5 text-sm font-medium text-white shadow-sm transition hover:bg-emerald-700 disabled:cursor-not-allowed disabled:bg-slate-300"
          title={
            isTerminal
              ? `Already ${approvalState}`
              : reviewQueueEmpty
                ? ""
                : "Review queue is not empty"
          }
        >
          {busy ? "…" : "Approve"}
        </button>
        <input
          type="text"
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          placeholder="Rejection reason (required to reject)"
          className="flex-1 rounded-md border border-slate-200 px-3 py-1.5 text-sm focus:border-brand focus:outline-none focus:ring-1 focus:ring-brand"
          disabled={busy || isTerminal}
        />
        <button
          type="button"
          disabled={busy || isTerminal || !reason.trim()}
          onClick={reject}
          className="rounded-md bg-rose-600 px-4 py-1.5 text-sm font-medium text-white shadow-sm transition hover:bg-rose-700 disabled:cursor-not-allowed disabled:bg-slate-300"
        >
          {busy ? "…" : "Reject"}
        </button>
      </div>
      <div className="flex flex-wrap items-center gap-2 px-5 py-2 text-xs">
        <span className="text-slate-500">Reason tags (optional):</span>
        {REJECTION_TAGS.map(([id, label]) => {
          const active = tags.includes(id);
          return (
            <button
              key={id}
              type="button"
              onClick={() => toggleTag(id)}
              disabled={busy || isTerminal}
              className={`rounded-full px-2 py-0.5 text-xs ring-1 ring-inset transition ${
                active
                  ? "bg-rose-100 text-rose-700 ring-rose-200"
                  : "bg-white text-slate-600 ring-slate-200 hover:bg-slate-50"
              }`}
            >
              {label}
            </button>
          );
        })}
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
