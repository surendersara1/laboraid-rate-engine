import { useEffect, useState } from "react";
import { api } from "../lib/api";

export function Audit(): JSX.Element {
  const [rows, setRows] = useState<unknown[]>([]);
  useEffect(() => {
    api
      .get<{ records: unknown[] }>("/v1/audit")
      .then((r) => setRows(r.records))
      .catch(() => setRows([]));
  }, []);
  return (
    <div>
      <h2 className="mb-4 text-xl font-semibold">Audit log</h2>
      <p className="text-sm text-slate-600">{rows.length} entries.</p>
    </div>
  );
}
