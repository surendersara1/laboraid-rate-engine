import { useMemo } from "react";
import type { RateCell } from "../types/api";

// Pivoted rate-sheet view (Spec/09 §1.5 panel 2, hero view): classifications +
// indenture cohorts as ROWS, funds as COLUMNS — matching the client's Excel
// layout. Built from the same flat `cells` the cell table uses; cohort lives in
// `dimensions` so the two apprentice cohorts stay distinct rows.

const COHORT_BEFORE = "Indentured Date is Before";
const COHORT_AFTER = "Indentured Date is After";

// Canonical-ish ordering: wage block first, then everything else in the order
// it first appears. Keeps Wage / OT columns on the left like the client sheet.
const WAGE_ORDER = ["Wage", "Wage Differential", "Wage 1.1x", "Wage 1.5x", "Wage 2.0x"];

type Row = {
  zone: string;
  pkg: string;
  before: string;
  after: string;
  cohortKey: string;
  values: Record<string, RateCell>;
};

function fmt(cell: RateCell | undefined): string {
  if (!cell || cell.value === null || cell.value === undefined) return "—";
  const v = cell.value;
  if (cell.value_type === "percent") return `${v.toFixed(2)}%`;
  return v.toFixed(2);
}

export function RateSheetGrid({
  cells,
  onSelect,
}: {
  cells: RateCell[];
  onSelect?: (cell: RateCell) => void;
}): JSX.Element {
  const { rows, fundCols, hasCohorts, hasZones } = useMemo(() => {
    const rowMap = new Map<string, Row>();
    const fundSeen: string[] = [];
    let cohorts = false;
    const zones = new Set<string>();

    for (const c of cells) {
      const dims = c.dimensions ?? {};
      const before = dims[COHORT_BEFORE] ?? "";
      const after = dims[COHORT_AFTER] ?? "";
      if (before || after) cohorts = true;
      zones.add(c.zone || "");
      const key = `${c.zone}||${c.package}||${before}||${after}`;
      let row = rowMap.get(key);
      if (!row) {
        row = { zone: c.zone, pkg: c.package, before, after, cohortKey: key, values: {} };
        rowMap.set(key, row);
      }
      row.values[c.column_name] = c;
      if (!fundSeen.includes(c.column_name)) fundSeen.push(c.column_name);
    }

    const fundCols = [
      ...WAGE_ORDER.filter((w) => fundSeen.includes(w)),
      ...fundSeen.filter((f) => !WAGE_ORDER.includes(f)),
    ];

    // Order rows: by zone, then base classifications (no cohort) before
    // apprentices, then by cohort grouping.
    const rows = [...rowMap.values()].sort((a, b) => {
      if (a.zone !== b.zone) return a.zone.localeCompare(b.zone);
      const aAppr = /apprentice|trainee/i.test(a.pkg) ? 1 : 0;
      const bAppr = /apprentice|trainee/i.test(b.pkg) ? 1 : 0;
      if (aAppr !== bAppr) return aAppr - bAppr;
      if (a.after !== b.after) return a.after.localeCompare(b.after);
      return a.pkg.localeCompare(b.pkg);
    });

    return { rows, fundCols, hasCohorts: cohorts, hasZones: zones.size > 1 };
  }, [cells]);

  const fmtDate = (s: string) => {
    if (!s) return "";
    const m = s.match(/^(\d{4})-(\d{2})-(\d{2})$/);
    return m ? `${+m[2]}/${+m[3]}/${m[1].slice(2)}` : s;
  };

  return (
    <div className="overflow-x-auto rounded-lg border border-slate-200">
      <table className="min-w-full border-collapse text-sm">
        <thead className="sticky top-0 z-10 bg-slate-100 text-left text-xs font-semibold uppercase tracking-wide text-slate-600">
          <tr>
            {hasZones && <th className="border-b border-r border-slate-200 px-3 py-2">Zone</th>}
            {hasCohorts && (
              <>
                <th className="border-b border-slate-200 px-2 py-2 text-center">Ind. Before</th>
                <th className="border-b border-r border-slate-200 px-2 py-2 text-center">Ind. After</th>
              </>
            )}
            <th className="border-b border-r border-slate-200 px-3 py-2">Classification</th>
            {fundCols.map((f) => (
              <th key={f} className="border-b border-slate-200 px-3 py-2 text-right whitespace-nowrap">
                {f}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100">
          {rows.map((row) => (
            <tr key={row.cohortKey} className="hover:bg-amber-50/50">
              {hasZones && (
                <td className="border-r border-slate-100 px-3 py-1.5 text-slate-500">{row.zone}</td>
              )}
              {hasCohorts && (
                <>
                  <td className="px-2 py-1.5 text-center text-xs text-slate-500">{fmtDate(row.before)}</td>
                  <td className="border-r border-slate-100 px-2 py-1.5 text-center text-xs text-slate-500">
                    {fmtDate(row.after)}
                  </td>
                </>
              )}
              <td className="border-r border-slate-100 px-3 py-1.5 font-medium text-slate-900 whitespace-nowrap">
                {row.pkg}
              </td>
              {fundCols.map((f) => {
                const cell = row.values[f];
                return (
                  <td
                    key={f}
                    className={`px-3 py-1.5 text-right font-mono tabular-nums ${
                      cell ? "text-slate-800 cursor-pointer hover:text-brand" : "text-slate-300"
                    }`}
                    onClick={() => cell && onSelect?.(cell)}
                  >
                    {fmt(cell)}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
