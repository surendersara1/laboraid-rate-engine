import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../lib/api";
import type { RateSheetSummary } from "../types/api";

export function Inbox(): JSX.Element {
  const [items, setItems] = useState<RateSheetSummary[]>([]);
  useEffect(() => {
    api
      .get<{ records: RateSheetSummary[] }>(
        "/v1/unions/all/rate-sheets?approval_state=pending_review",
      )
      .then((r) => setItems(r.records ?? []))
      .catch(() => setItems([]));
  }, []);

  return (
    <div>
      <h2 className="mb-4 text-xl font-semibold">Inbox · pending review</h2>
      {items.length === 0 ? (
        <p className="text-slate-500">Nothing waiting for review.</p>
      ) : (
        <ul className="space-y-2">
          {items.map((i) => (
            <li key={`${i.union}-${i.period}`} className="rounded border bg-white p-3">
              <Link
                className="text-brand underline"
                to={`/business/rate-sheets/${i.union}/${i.period}`}
              >
                {i.union} · {i.period}
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
