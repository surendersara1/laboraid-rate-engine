import { useState } from "react";
import { api } from "../lib/api";

// Per-row comment modal (Spec/09 §1.5 "comment per row"). Writes via
// POST /v1/cells/{cell_id}/comment (audit D7). Controlled by RateCellTable.
export function CellCommentModal({
  cellId,
  onClose,
}: {
  cellId: string;
  onClose: () => void;
}): JSX.Element {
  const [text, setText] = useState("");
  const [saving, setSaving] = useState(false);

  const save = async () => {
    if (!text.trim()) return;
    setSaving(true);
    try {
      await api.post(`/v1/cells/${cellId}/comment`, {
        comment: text.trim(),
        timestamp: new Date().toISOString(),
      });
      onClose();
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="fixed inset-0 flex items-center justify-center bg-black/40">
      <div className="w-96 space-y-3 rounded-lg bg-white p-4">
        <h3 className="font-semibold">Comment on cell</h3>
        <textarea
          className="h-24 w-full rounded border px-2 py-1"
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="Add a note for this row…"
        />
        <div className="flex justify-end gap-2">
          <button className="px-3 py-1" onClick={onClose}>
            Cancel
          </button>
          <button
            className="rounded bg-brand px-3 py-1 text-white disabled:opacity-50"
            onClick={save}
            disabled={saving || !text.trim()}
          >
            Save
          </button>
        </div>
      </div>
    </div>
  );
}
