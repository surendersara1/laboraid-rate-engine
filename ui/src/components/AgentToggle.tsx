import { useState } from "react";
import { api } from "../lib/api";
import { useUserStore } from "../lib/store";

// Admins-only enable/disable toggle -> PATCH /v1/agents/{name} (Spec/09 §1.4).
export function AgentToggle({
  name,
  enabled,
  onChange,
}: {
  name: string;
  enabled: boolean;
  onChange: (enabled: boolean) => void;
}): JSX.Element {
  const groups = useUserStore((s) => s.groups);
  const isAdmin = groups.includes("Admins");
  const [busy, setBusy] = useState(false);

  const toggle = async () => {
    setBusy(true);
    try {
      await api.patch(`/v1/agents/${name}`, { enabled: !enabled });
      onChange(!enabled);
    } finally {
      setBusy(false);
    }
  };

  return (
    <button
      disabled={!isAdmin || busy}
      onClick={toggle}
      className={`rounded px-3 py-1 text-sm text-white disabled:opacity-40 ${
        enabled ? "bg-green-600" : "bg-slate-400"
      }`}
      title={isAdmin ? "" : "Admins only"}
    >
      {enabled ? "Enabled" : "Disabled"}
    </button>
  );
}
