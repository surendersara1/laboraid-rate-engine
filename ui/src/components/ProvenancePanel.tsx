import { useOverrideStore } from "../lib/store";
import type { RateCell } from "../types/api";

// Per-cell provenance + confidence side panel (Tier 1.5).
// Renders the selected cell as an inspector: who extracted this number,
// from where, with what confidence. Unknown provenance keys fall into a
// "Raw" expandable section so nothing is hidden from the reviewer.
const METHOD_LABEL: Record<string, string> = {
  kernel: "Deterministic kernel (pdfplumber → rapidocr)",
  bedrock_multimodal: "Bedrock Claude multimodal (Path B)",
  claude_generic: "Generic Claude (Path C)",
  pdfplumber: "Deterministic kernel (pdfplumber)",
};

export function ProvenancePanel({
  cell,
  onComment,
}: {
  cell: RateCell | null;
  onComment?: (cell: RateCell) => void;
}): JSX.Element {
  const openOverride = useOverrideStore((s) => s.open);

  if (!cell) {
    return (
      <div className="p-6 text-center text-sm text-slate-500">
        Click a row in the table to inspect provenance.
      </div>
    );
  }

  const prov = (cell.provenance ?? {}) as Record<string, unknown>;
  const source = String(prov.source ?? prov.method ?? "");
  const methodLabel = METHOD_LABEL[source] ?? source ?? "unknown";
  const page = prov.page ?? prov.page_number;
  const line = prov.line ?? prov.row;
  const confidencePct = cell.confidence * 100;
  const tone =
    confidencePct >= 95
      ? "bg-emerald-50 text-emerald-700 ring-emerald-200"
      : confidencePct >= 80
        ? "bg-amber-50 text-amber-700 ring-amber-200"
        : "bg-rose-50 text-rose-700 ring-rose-200";

  const known = new Set(["source", "method", "page", "page_number", "line", "row"]);
  const extra = Object.fromEntries(
    Object.entries(prov).filter(([k]) => !known.has(k)),
  );

  return (
    <div className="space-y-4 p-4 text-sm">
      <div>
        <div className="text-xs font-medium uppercase tracking-wide text-slate-500">
          Selected cell
        </div>
        <div className="mt-1 font-medium text-slate-900">{cell.package}</div>
        <div className="text-slate-600">{cell.column_name}</div>
      </div>

      <div className="grid grid-cols-2 gap-3">
        <div>
          <div className="text-xs uppercase tracking-wide text-slate-500">Value</div>
          <div className="mt-0.5 font-mono text-lg tabular-nums text-slate-900">
            {cell.value == null ? "—" : Number(cell.value).toFixed(2)}
          </div>
        </div>
        <div>
          <div className="text-xs uppercase tracking-wide text-slate-500">
            Confidence
          </div>
          <span
            className={`mt-0.5 inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${tone}`}
          >
            {confidencePct.toFixed(0)} %
          </span>
        </div>
      </div>

      <div className="rounded-md border border-slate-200 bg-slate-50 p-3 text-xs">
        <div className="text-xs font-medium uppercase tracking-wide text-slate-500">
          Provenance
        </div>
        <dl className="mt-2 space-y-1">
          <div className="flex justify-between gap-2">
            <dt className="text-slate-500">Zone</dt>
            <dd className="font-medium text-slate-800">{cell.zone || "—"}</dd>
          </div>
          <div className="flex justify-between gap-2">
            <dt className="text-slate-500">Method</dt>
            <dd className="font-medium text-slate-800">{methodLabel}</dd>
          </div>
          {page != null && (
            <div className="flex justify-between gap-2">
              <dt className="text-slate-500">Page</dt>
              <dd className="font-mono tabular-nums text-slate-800">{String(page)}</dd>
            </div>
          )}
          {line != null && (
            <div className="flex justify-between gap-2">
              <dt className="text-slate-500">Line / Row</dt>
              <dd className="font-mono tabular-nums text-slate-800">{String(line)}</dd>
            </div>
          )}
        </dl>
        {Object.keys(extra).length > 0 && (
          <details className="mt-2">
            <summary className="cursor-pointer text-xs text-slate-500">
              Raw provenance
            </summary>
            <pre className="mt-2 overflow-auto rounded bg-slate-900 p-2 font-mono text-xs leading-tight text-slate-100">
              {JSON.stringify(extra, null, 2)}
            </pre>
          </details>
        )}
      </div>

      <div className="flex gap-2">
        <button
          type="button"
          onClick={() => onComment?.(cell)}
          className="flex-1 rounded-md border border-sky-200 bg-sky-50 px-3 py-1.5 text-xs font-medium text-sky-700 hover:bg-sky-100"
        >
          💬 Comment
        </button>
        <button
          type="button"
          onClick={() =>
            openOverride(
              cell.cell_id,
              `${cell.package} · ${cell.column_name}`,
              cell.value == null ? "" : String(cell.value),
            )
          }
          className="flex-1 rounded-md border border-amber-200 bg-amber-50 px-3 py-1.5 text-xs font-medium text-amber-700 hover:bg-amber-100"
        >
          ✎ Override
        </button>
      </div>
    </div>
  );
}
