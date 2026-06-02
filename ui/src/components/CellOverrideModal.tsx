import { api } from "../lib/api";
import { useOverrideStore } from "../lib/store";

// Manual cell override modal (Spec/09 §1.5). Writes via POST /v1/cells/{id}/override.
export function CellOverrideModal(): JSX.Element | null {
  const { cellId, value, setValue, close } = useOverrideStore();
  if (!cellId) return null;

  const save = async () => {
    await api.post(`/v1/cells/${cellId}/override`, {
      value,
      scope: "laboraid",
      timestamp: new Date().toISOString(),
    });
    close();
  };

  return (
    <div className="fixed inset-0 flex items-center justify-center bg-black/40">
      <div className="w-96 space-y-3 rounded-lg bg-white p-4">
        <h3 className="font-semibold">Override cell</h3>
        <input
          className="w-full rounded border px-2 py-1"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          placeholder="New value"
        />
        <div className="flex justify-end gap-2">
          <button className="px-3 py-1" onClick={close}>
            Cancel
          </button>
          <button
            className="rounded bg-brand px-3 py-1 text-white"
            onClick={save}
          >
            Save
          </button>
        </div>
      </div>
    </div>
  );
}
