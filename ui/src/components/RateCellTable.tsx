import {
  createColumnHelper,
  flexRender,
  getCoreRowModel,
  useReactTable,
} from "@tanstack/react-table";
import type { RateCell } from "../types/api";

const col = createColumnHelper<RateCell>();
const columns = [
  col.accessor("zone", { header: "Zone" }),
  col.accessor("package", { header: "Package" }),
  col.accessor("column_name", { header: "Column" }),
  col.accessor("value", { header: "Value" }),
  col.accessor("confidence", {
    header: "Conf.",
    cell: (c) => `${(c.getValue() * 100).toFixed(0)}%`,
  }),
];

// Extracted rate sheet as a table (Spec/09 §1.5 panel 2).
export function RateCellTable({
  cells,
  onSelect,
}: {
  cells: RateCell[];
  onSelect: (cell: RateCell) => void;
}): JSX.Element {
  const table = useReactTable({
    data: cells,
    columns,
    getCoreRowModel: getCoreRowModel(),
  });
  return (
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
}
