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
  llm_claude: "Bedrock Claude (LLM extractor)",
  derived: "Derived (Publisher post-step computation)",
  zero_by_rule: "Zero by rule (Local convention)",
};

// Icon + tint per method — derived and zero-by-rule get their own visual
// treatment so the reviewer can spot computed cells at a glance.
const METHOD_BADGE: Record<string, { icon: string; tone: string; label: string }> = {
  derived: {
    icon: "ƒ",
    tone: "bg-violet-50 text-violet-800 ring-violet-200",
    label: "derived",
  },
  zero_by_rule: {
    icon: "0",
    tone: "bg-slate-100 text-slate-700 ring-slate-300",
    label: "zero by rule",
  },
  kernel: {
    icon: "▣",
    tone: "bg-emerald-50 text-emerald-800 ring-emerald-200",
    label: "kernel",
  },
  llm_claude: {
    icon: "✦",
    tone: "bg-sky-50 text-sky-800 ring-sky-200",
    label: "LLM",
  },
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
  const methodKey = String(prov.method ?? prov.source ?? "");
  const methodLabel = METHOD_LABEL[methodKey] ?? methodKey ?? "unknown";
  const badge = METHOD_BADGE[methodKey];
  const derivedFrom = prov.derived_from as string | undefined;
  const zeroByRuleText = prov.rule as string | undefined;
  const sourcePdf = prov.source_pdf as string | undefined;
  const conflicts = (prov.conflicts as
    | Array<{ rejected_value: number; source_pdf: string; method: string }>
    | undefined) ?? [];
  const page = prov.page ?? prov.page_number;
  const line = prov.line ?? prov.row;
  const confidencePct = cell.confidence * 100;
  const tone =
    confidencePct >= 95
      ? "bg-emerald-50 text-emerald-700 ring-emerald-200"
      : confidencePct >= 80
        ? "bg-amber-50 text-amber-700 ring-amber-200"
        : "bg-rose-50 text-rose-700 ring-rose-200";

  const known = new Set([
    "source", "method", "page", "page_number", "line", "row",
    "source_pdf", "derived_from", "rule", "row_raw",
  ]);
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
            <dd className="font-medium text-slate-800">
              {badge && (
                <span
                  className={`mr-1 inline-flex h-4 w-4 items-center justify-center rounded-full text-[10px] font-bold ring-1 ring-inset ${badge.tone}`}
                  title={badge.label}
                >
                  {badge.icon}
                </span>
              )}
              {methodLabel}
            </dd>
          </div>
          {sourcePdf && (
            <div className="flex justify-between gap-2">
              <dt className="text-slate-500">Source PDF</dt>
              <dd className="max-w-[60%] truncate text-right font-mono text-xs text-slate-800" title={sourcePdf}>
                {sourcePdf}
              </dd>
            </div>
          )}
          {derivedFrom && (
            <div className="flex justify-between gap-2">
              <dt className="text-slate-500">Derived from</dt>
              <dd className="font-mono text-xs text-slate-800">{derivedFrom}</dd>
            </div>
          )}
          {zeroByRuleText && (
            <div className="flex flex-col gap-1">
              <dt className="text-slate-500">Rule</dt>
              <dd className="text-xs text-slate-700">{zeroByRuleText}</dd>
            </div>
          )}
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

      {conflicts.length > 0 && (
        <div className="rounded-md border border-amber-300 bg-amber-50 p-3 text-xs">
          <div className="flex items-center gap-2">
            <span className="inline-flex h-5 w-5 items-center justify-center rounded-full bg-amber-200 text-amber-900 ring-1 ring-amber-300">
              !
            </span>
            <span className="font-semibold text-amber-900">
              Value conflict — {conflicts.length} other source
              {conflicts.length === 1 ? "" : "s"} disagreed
            </span>
          </div>
          <p className="mt-1 text-amber-800">
            The first source's value won (above). The conflicting values
            are recorded for audit:
          </p>
          <ul className="mt-2 space-y-1">
            {conflicts.map((c, i) => (
              <li key={i} className="flex justify-between gap-2">
                <span className="truncate font-mono text-amber-900" title={c.source_pdf}>
                  {c.source_pdf?.split("/").slice(-1)[0] || "(unknown)"}
                </span>
                <span className="font-mono tabular-nums font-semibold text-amber-900">
                  {Number(c.rejected_value).toFixed(2)}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}

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
