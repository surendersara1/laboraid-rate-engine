import type { RateCell } from "../types/api";

// Per-cell provenance + confidence side panel (Spec/09 §1.5 panel 3).
export function ProvenancePanel({ cell }: { cell: RateCell | null }): JSX.Element {
  if (!cell) {
    return <div className="p-4 text-sm text-slate-500">Select a cell.</div>;
  }
  return (
    <div className="space-y-2 p-4 text-sm">
      <h3 className="font-semibold">
        {cell.zone} / {cell.package} / {cell.column_name}
      </h3>
      <p>Value: {cell.value ?? "(blank)"}</p>
      <p>Confidence: {(cell.confidence * 100).toFixed(0)}%</p>
      <pre className="overflow-auto rounded bg-slate-100 p-2 text-xs">
        {JSON.stringify(cell.provenance ?? {}, null, 2)}
      </pre>
    </div>
  );
}
