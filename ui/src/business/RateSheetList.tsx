import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../lib/api";
import type { RateSheetSummary } from "../types/api";

interface Props {
  /** approval_state to filter by (pending_review | approved | rejected). */
  state: string;
  title: string;
  subtitle: string;
  /** Tailwind classes for the count pill. */
  badge: string;
  emptyIcon: string;
  emptyMsg: string;
}

/** Shared rate-sheet card grid for the Business review tabs. Reads the
 *  ratesheet-list endpoint filtered by approval_state — the same source the
 *  Inbox uses. (Rate-sheet operations data; cell content stays in Aurora.) */
export function RateSheetList({
  state,
  title,
  subtitle,
  badge,
  emptyIcon,
  emptyMsg,
}: Props): JSX.Element {
  const [items, setItems] = useState<RateSheetSummary[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    api
      .get<{ records: RateSheetSummary[] }>(
        `/v1/unions/all/rate-sheets?approval_state=${encodeURIComponent(state)}`,
      )
      .then((r) => setItems(r.records ?? []))
      .catch(() => setItems([]))
      .finally(() => setLoading(false));
  }, [state]);

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-2xl font-semibold text-slate-900">{title}</h2>
          <p className="text-sm text-slate-500">{subtitle}</p>
        </div>
        <span
          className={`rounded-full px-3 py-1 text-xs font-medium ring-1 ring-inset ${badge}`}
        >
          {items.length}
        </span>
      </div>
      {loading ? (
        <p className="text-slate-500">Loading…</p>
      ) : items.length === 0 ? (
        <div className="rounded-lg border border-slate-200 bg-white p-12 text-center shadow-sm">
          <div className="mx-auto mb-3 text-4xl">{emptyIcon}</div>
          <p className="font-medium text-slate-700">{emptyMsg}</p>
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
                Open →
              </div>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}
