import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../lib/api";
import type { RateSheetSummary } from "../types/api";

const STATE_PILL: Record<string, string> = {
  pending_review: "bg-amber-100 text-amber-800 ring-amber-200",
  approved: "bg-emerald-100 text-emerald-800 ring-emerald-200",
  rejected: "bg-rose-100 text-rose-800 ring-rose-200",
  published: "bg-indigo-100 text-indigo-800 ring-indigo-200",
};

export function ByUnion(): JSX.Element {
  const { union } = useParams();
  const [items, setItems] = useState<RateSheetSummary[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    // No state filter → return every period; URL union "all" means cross-union.
    const local = union ? union.match(/(\d{2,4})\s*$/)?.[1] ?? union : "all";
    api
      .get<{ records: RateSheetSummary[] }>(`/v1/unions/${local}/rate-sheets`)
      .then((r) => setItems(r.records ?? []))
      .catch(() => setItems([]))
      .finally(() => setLoading(false));
  }, [union]);

  return (
    <div className="space-y-4">
      <div>
        <h2 className="text-2xl font-semibold text-slate-900">
          By Union {union ? `· ${union}` : ""}
        </h2>
        <p className="text-sm text-slate-500">
          All rate sheets, every status. Click a row to review.
        </p>
      </div>
      {loading ? (
        <p className="text-slate-500">Loading…</p>
      ) : items.length === 0 ? (
        <div className="rounded-lg border border-slate-200 bg-white p-8 text-center">
          <p className="text-slate-500">No rate sheets yet.</p>
        </div>
      ) : (
        <div className="overflow-hidden rounded-lg border border-slate-200 bg-white shadow-sm">
          <table className="w-full text-sm">
            <thead className="bg-slate-50 text-left text-xs font-medium uppercase tracking-wide text-slate-500">
              <tr>
                <th className="px-4 py-3">Union</th>
                <th className="px-4 py-3">Period</th>
                <th className="px-4 py-3">State</th>
                <th className="px-4 py-3 text-right">Gaps</th>
                <th className="px-4 py-3"></th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {items.map((i) => (
                <tr key={`${i.union}-${i.period}`} className="hover:bg-slate-50">
                  <td className="px-4 py-3 font-medium text-slate-900">{i.union}</td>
                  <td className="px-4 py-3 text-slate-700">{i.period}</td>
                  <td className="px-4 py-3">
                    <span
                      className={`inline-flex items-center rounded-full px-2 py-1 text-xs font-medium ring-1 ring-inset ${
                        STATE_PILL[i.approval_state] ?? "bg-slate-100 text-slate-700"
                      }`}
                    >
                      {i.approval_state.replace("_", " ")}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-right tabular-nums text-slate-700">
                    {i.gap_count ?? 0}
                  </td>
                  <td className="px-4 py-3 text-right">
                    <Link
                      to={`/business/rate-sheets/${i.union}/${i.period}`}
                      className="text-brand hover:text-brand-dark"
                    >
                      Review →
                    </Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
