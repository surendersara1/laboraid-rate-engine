import { useState } from "react";
import { api } from "../lib/api";
import { useOverrideStore } from "../lib/store";

// Manual cell override modal (Tier 2.4). POST /v1/cells/{id}/override with
// {value, justification}; the Lambda writes a DDB row + audit_log entry.
export function CellOverrideModal({
  onSaved,
}: {
  onSaved?: () => void;
}): JSX.Element | null {
  const {
    cellId,
    cellLabel,
    currentValue,
    value,
    justification,
    setValue,
    setJustification,
    close,
  } = useOverrideStore();
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  if (!cellId) return null;

  const save = async () => {
    setError("");
    setSaving(true);
    try {
      await api.post(`/v1/cells/${cellId}/override`, {
        value: Number(value),
        justification,
      });
      onSaved?.();
      close();
    } catch (e) {
      setError(String(e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
      <div className="w-[460px] space-y-4 rounded-lg bg-white p-5 shadow-xl">
        <div>
          <h3 className="text-base font-semibold text-slate-900">
            Override cell value
          </h3>
          {cellLabel && (
            <p className="mt-0.5 text-xs text-slate-500">{cellLabel}</p>
          )}
        </div>
        <div className="rounded-md border border-slate-200 bg-slate-50 px-3 py-2 text-sm">
          <span className="text-slate-500">Current value:</span>{" "}
          <span className="font-mono tabular-nums font-medium text-slate-800">
            {currentValue || "—"}
          </span>
        </div>
        <div>
          <label className="text-xs font-medium uppercase tracking-wide text-slate-500">
            New value
          </label>
          <input
            type="number"
            step="0.01"
            className="mt-1 w-full rounded-md border border-slate-200 px-3 py-1.5 font-mono text-sm tabular-nums focus:border-brand focus:outline-none focus:ring-1 focus:ring-brand"
            value={value}
            onChange={(e) => setValue(e.target.value)}
            autoFocus
          />
        </div>
        <div>
          <label className="text-xs font-medium uppercase tracking-wide text-slate-500">
            Justification (optional)
          </label>
          <textarea
            className="mt-1 h-20 w-full rounded-md border border-slate-200 px-3 py-2 text-sm focus:border-brand focus:outline-none focus:ring-1 focus:ring-brand"
            value={justification}
            onChange={(e) => setJustification(e.target.value)}
            placeholder="Why is this override correct? (Visible in activity log)"
          />
        </div>
        {error && <p className="text-xs text-rose-600">{error}</p>}
        <div className="flex justify-end gap-2">
          <button
            type="button"
            className="rounded-md px-3 py-1.5 text-sm text-slate-700 hover:bg-slate-100"
            onClick={close}
            disabled={saving}
          >
            Cancel
          </button>
          <button
            type="button"
            className="rounded-md bg-amber-600 px-3 py-1.5 text-sm font-medium text-white shadow-sm hover:bg-amber-700 disabled:cursor-not-allowed disabled:bg-slate-300"
            onClick={save}
            disabled={saving || value === "" || value === currentValue}
          >
            {saving ? "Saving…" : "Apply override"}
          </button>
        </div>
      </div>
    </div>
  );
}
