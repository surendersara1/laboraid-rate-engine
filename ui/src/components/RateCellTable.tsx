import {
  createColumnHelper,
  flexRender,
  getCoreRowModel,
  useReactTable,
} from "@tanstack/react-table";
import { useState } from "react";
import type { RateCell } from "../types/api";
import { CellCommentModal } from "./CellCommentModal";

const col = createColumnHelper<RateCell>();

// Extracted rate sheet as a table (Spec/09 §1.5 panel 2). The trailing column
// exposes a per-row comment affordance (Spec/09 §1.5 "comment per row", audit D7).
export function RateCellTable({
  cells,
  onSelect,
}: {
  cells: RateCell[];
  onSelect: (cell: RateCell) => void;
}): JSX.Element {
  const [commentCellId, setCommentCellId] = useState<string | null>(null);

  const columns = [
    col.accessor("zone", {
      header: "Zone",
      cell: (c) => (
        <span className="text-xs font-medium uppercase tracking-wide text-slate-500">
          {c.getValue()}
        </span>
      ),
    }),
    col.accessor("package", {
      header: "Package",
      cell: (c) => <span className="font-medium text-slate-900">{c.getValue()}</span>,
    }),
    col.accessor("column_name", { header: "Column" }),
    col.accessor("value", {
      header: "Value",
      cell: (c) => {
        const v = c.getValue();
        return (
          <span className="font-mono tabular-nums text-slate-900">
            {typeof v === "number" ? v.toFixed(2) : v ?? "—"}
          </span>
        );
      },
    }),
    col.accessor("confidence", {
      header: "Conf.",
      cell: (c) => {
        const pct = c.getValue() * 100;
        const tone =
          pct >= 95
            ? "bg-emerald-50 text-emerald-700 ring-emerald-200"
            : pct >= 80
              ? "bg-amber-50 text-amber-700 ring-amber-200"
              : "bg-rose-50 text-rose-700 ring-rose-200";
        return (
          <span
            className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${tone}`}
          >
            {pct.toFixed(0)}%
          </span>
        );
      },
    }),
    col.display({
      id: "comment",
      header: "",
      cell: (c) => (
        <button
          className="rounded px-2 py-0.5 text-xs text-brand hover:bg-brand/10 hover:text-brand-dark"
          title="Comment on this row"
          onClick={(e) => {
            e.stopPropagation();
            setCommentCellId(c.row.original.cell_id);
          }}
        >
          💬
        </button>
      ),
    }),
  ];

  const table = useReactTable({
    data: cells,
    columns,
    getCoreRowModel: getCoreRowModel(),
  });

  const tableEl = (
    <table className="w-full text-sm">
      <thead className="sticky top-0 z-10 bg-slate-50 text-left text-xs font-medium uppercase tracking-wide text-slate-500">
        {table.getHeaderGroups().map((hg) => (
          <tr key={hg.id}>
            {hg.headers.map((h) => (
              <th key={h.id} className="border-b border-slate-200 px-3 py-2">
                {flexRender(h.column.columnDef.header, h.getContext())}
              </th>
            ))}
          </tr>
        ))}
      </thead>
      <tbody className="divide-y divide-slate-100">
        {table.getRowModel().rows.map((row) => (
          <tr
            key={row.id}
            className="cursor-pointer transition hover:bg-amber-50"
            onClick={() => onSelect(row.original)}
          >
            {row.getVisibleCells().map((c) => (
              <td key={c.id} className="px-3 py-2 align-middle">
                {flexRender(c.column.columnDef.cell, c.getContext())}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );

  return (
    <>
      {tableEl}
      {commentCellId && (
        <CellCommentModal
          cellId={commentCellId}
          onClose={() => setCommentCellId(null)}
        />
      )}
    </>
  );
}
