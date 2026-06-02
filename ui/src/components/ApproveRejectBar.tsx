import { useState } from "react";
import { api } from "../lib/api";

// Business top bar: Approve / Reject (Spec/09 §1.5). Approve is disabled until
// the review queue is empty; Reject requires a reason.
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
  const [busy, setBusy] = useState(false);
  const base = `/v1/unions/${union}/rate-sheets/${period}`;

  const approve = async () => {
    setBusy(true);
    try {
      await api.post(`${base}/approve`, {
        approval_state: approvalState,
        review_queue_empty: reviewQueueEmpty,
      });
      onChanged("approved");
    } finally {
      setBusy(false);
    }
  };

  const reject = async () => {
    if (!reason.trim()) return;
    setBusy(true);
    try {
      await api.post(`${base}/reject`, { approval_state: approvalState, reason });
      onChanged("rejected");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex items-center gap-3 border-b bg-white px-4 py-2">
      <span className="text-sm font-medium">State: {approvalState}</span>
      <button
        disabled={!reviewQueueEmpty || busy}
        onClick={approve}
        className="rounded bg-green-600 px-4 py-1 text-white disabled:opacity-40"
        title={reviewQueueEmpty ? "" : "Clear the review queue first"}
      >
        Approve
      </button>
      <input
        className="flex-1 rounded border px-2 py-1 text-sm"
        placeholder="Rejection reason (required to reject)"
        value={reason}
        onChange={(e) => setReason(e.target.value)}
      />
      <button
        disabled={!reason.trim() || busy}
        onClick={reject}
        className="rounded bg-red-600 px-4 py-1 text-white disabled:opacity-40"
      >
        Reject
      </button>
    </div>
  );
}
