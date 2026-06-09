import { useState } from "react";
import { api } from "../lib/api";

// Per-cell comment modal (Tier 2.3). POST /v1/cells/{cell_id}/comment with
// {text: ...}; the Lambda writes an audit_log row tagged with the cell's
// (union, period) so the Activity timeline picks it up automatically.
export function CellCommentModal({
  cellId,
  cellLabel,
  onClose,
  onSaved,
}: {
  cellId: string;
  cellLabel?: string;
  onClose: () => void;
  onSaved?: () => void;
}): JSX.Element {
  const [text, setText] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  const save = async () => {
    if (!text.trim()) return;
    setSaving(true);
    setError("");
    try {
      await api.post(`/v1/cells/${cellId}/comment`, { text: text.trim() });
      onSaved?.();
      onClose();
    } catch (e) {
      setError(String(e));
      setSaving(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
      <div className="w-[440px] space-y-4 rounded-lg bg-white p-5 shadow-xl">
        <div>
          <h3 className="text-base font-semibold text-slate-900">
            Comment on cell
          </h3>
          {cellLabel && (
            <p className="mt-0.5 text-xs text-slate-500">{cellLabel}</p>
          )}
        </div>
        <textarea
          className="h-28 w-full rounded-md border border-slate-200 px-3 py-2 text-sm focus:border-brand focus:outline-none focus:ring-1 focus:ring-brand"
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="Add a note for this row — visible to all reviewers in the activity timeline…"
          autoFocus
        />
        {error && <p className="text-xs text-rose-600">{error}</p>}
        <div className="flex justify-end gap-2">
          <button
            type="button"
            className="rounded-md px-3 py-1.5 text-sm text-slate-700 hover:bg-slate-100"
            onClick={onClose}
            disabled={saving}
          >
            Cancel
          </button>
          <button
            type="button"
            className="rounded-md bg-brand px-3 py-1.5 text-sm font-medium text-white shadow-sm hover:bg-brand-dark disabled:cursor-not-allowed disabled:bg-slate-300"
            onClick={save}
            disabled={saving || !text.trim()}
          >
            {saving ? "Saving…" : "Save comment"}
          </button>
        </div>
      </div>
    </div>
  );
}
