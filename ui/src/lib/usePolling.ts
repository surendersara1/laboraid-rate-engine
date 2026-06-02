import { useEffect } from "react";

// Poll `fn` every `intervalMs` while `active` is true (Spec/09 §4 L1 — 5s
// polling on Jobs/Agents while any job is in_progress).
export function usePolling(
  fn: () => void,
  active: boolean,
  intervalMs = 5000,
): void {
  useEffect(() => {
    if (!active) return;
    const id = setInterval(fn, intervalMs);
    return () => clearInterval(id);
  }, [fn, active, intervalMs]);
}
