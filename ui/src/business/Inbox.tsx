import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../lib/api";
import type { RateSheetSummary } from "../types/api";

export function Inbox(): JSX.Element {
  const [items, setItems] = useState<RateSheetSummary[]>([]);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    api
      .get<{ records: RateSheetSummary[] }>(
        "/v1/unions/all/rate-sheets?approval_state=pending_review",
      )
      .then((r) => setItems(r.records ?? []))
      .catch(() => setItems([]))
      .finally(() => setLoading(false));
  }, []);

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-2xl font-semibold text-slate-900">Inbox</h2>
          <p className="text-sm text-slate-500">Rate sheets waiting for review.</p>
        </div>
        <span className="rounded-full bg-amber-100 px-3 py-1 text-xs font-medium text-amber-800 ring-1 ring-inset ring-amber-200">
          {items.length} pending
        </span>
      </div>
      {loading ? (
        <p className="text-slate-500">Loading…</p>
      ) : items.length === 0 ? (
        <div className="rounded-lg border border-slate-200 bg-white p-12 text-center shadow-sm">
          <div className="mx-auto mb-3 text-4xl">📬</div>
          <p className="font-medium text-slate-700">Nothing waiting for review.</p>
          <p className="mt-1 text-sm text-slate-500">
            New rate sheets show up here once the agent finishes extracting.
          </p>
        </div>
      ) : (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {items.map((i) => (
            <Link
              key={`${i.union}-${i.period}`}
              to={`/business/rate-sheets/${i.union}/${i.period}`}
              className="group block rounded-lg border border-slate-200 bg-white p-4 shadow-sm transition hover:border-brand hover:shadow-md"
            >
              <div className="flex items-start justify-between">
                <div>
                  <p className="text-xs font-medium uppercase tracking-wide text-slate-500">
                    {i.trade ?? "Union"}
                  </p>
                  <p className="mt-1 text-lg font-semibold text-slate-900 group-hover:text-brand">
                    {i.union}
                  </p>
                  <p className="mt-1 text-sm text-slate-600">{i.period}</p>
                </div>
                {(i.gap_count ?? 0) > 0 && (
                  <span className="rounded-full bg-rose-50 px-2 py-1 text-xs font-medium text-rose-700 ring-1 ring-inset ring-rose-200">
                    {i.gap_count} gap{i.gap_count === 1 ? "" : "s"}
                  </span>
                )}
              </div>
              <div className="mt-4 text-sm font-medium text-brand opacity-0 transition group-hover:opacity-100">
                Review →
              </div>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}
