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
    col.accessor("zone", { header: "Zone" }),
    col.accessor("package", { header: "Package" }),
    col.accessor("column_name", { header: "Column" }),
    col.accessor("value", { header: "Value" }),
    col.accessor("confidence", {
      header: "Conf.",
      cell: (c) => `${(c.getValue() * 100).toFixed(0)}%`,
    }),
    col.display({
      id: "comment",
      header: "",
      cell: (c) => (
        <button
          className="rounded px-2 py-0.5 text-xs text-brand hover:underline"
          title="Comment on this row"
          onClick={(e) => {
            e.stopPropagation();
            setCommentCellId(c.row.original.cell_id);
          }}
        >
          💬 Comment
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
    <table className="w-full border-collapse text-sm">
      <thead className="bg-slate-100">
        {table.getHeaderGroups().map((hg) => (
          <tr key={hg.id}>
            {hg.headers.map((h) => (
              <th key={h.id} className="border px-2 py-1 text-left">
                {flexRender(h.column.columnDef.header, h.getContext())}
              </th>
            ))}
          </tr>
        ))}
      </thead>
      <tbody>
        {table.getRowModel().rows.map((row) => (
          <tr
            key={row.id}
            className="cursor-pointer hover:bg-amber-50"
            onClick={() => onSelect(row.original)}
          >
            {row.getVisibleCells().map((c) => (
              <td key={c.id} className="border px-2 py-1">
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
