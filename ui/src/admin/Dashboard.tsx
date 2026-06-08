import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../lib/api";
import type { Job, RateSheetSummary } from "../types/api";

interface DashState {
  jobsInFlight: number;
  jobsFailed24h: number;
  pendingReview: number;
  approved: number;
}

export function Dashboard(): JSX.Element {
  const [stats, setStats] = useState<DashState>({
    jobsInFlight: 0,
    jobsFailed24h: 0,
    pendingReview: 0,
    approved: 0,
  });
  const [pending, setPending] = useState<RateSheetSummary[]>([]);

  useEffect(() => {
    Promise.all([
      api.get<{ jobs: Job[] }>("/v1/jobs").catch(() => ({ jobs: [] })),
      api
        .get<{ records: RateSheetSummary[] }>(
          "/v1/unions/all/rate-sheets?approval_state=pending_review",
        )
        .catch(() => ({ records: [] })),
      api
        .get<{ records: RateSheetSummary[] }>(
          "/v1/unions/all/rate-sheets?approval_state=approved",
        )
        .catch(() => ({ records: [] })),
    ]).then(([jobs, pendingResp, approvedResp]) => {
      setStats({
        jobsInFlight: jobs.jobs.filter((j) => j.status === "in_progress").length,
        jobsFailed24h: jobs.jobs.filter((j) => j.status === "failed").length,
        pendingReview: pendingResp.records.length,
        approved: approvedResp.records.length,
      });
      setPending(pendingResp.records.slice(0, 5));
    });
  }, []);

  const tiles: Array<[string, number, string]> = [
    ["Pending review", stats.pendingReview, "bg-amber-100 text-amber-800"],
    ["Approved", stats.approved, "bg-emerald-100 text-emerald-800"],
    ["Jobs in flight", stats.jobsInFlight, "bg-sky-100 text-sky-800"],
    ["Failed (24h)", stats.jobsFailed24h, "bg-rose-100 text-rose-800"],
  ];

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-2xl font-semibold text-slate-900">Dashboard</h2>
        <p className="text-sm text-slate-500">
          LaborAid Rate Engine · live pipeline status
        </p>
      </div>
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        {tiles.map(([label, value, pill]) => (
          <div
            key={label}
            className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm"
          >
            <p className="text-sm font-medium text-slate-500">{label}</p>
            <p className="mt-2 text-3xl font-semibold text-slate-900">{value}</p>
            <span
              className={`mt-3 inline-block rounded-full px-2 py-0.5 text-xs ${pill}`}
            >
              {value > 0 ? "active" : "ok"}
            </span>
          </div>
        ))}
      </div>
      <div className="rounded-lg border border-slate-200 bg-white shadow-sm">
        <div className="flex items-center justify-between border-b border-slate-100 px-5 py-3">
          <h3 className="text-sm font-semibold text-slate-900">
            Latest pending review
          </h3>
          <Link
            to="/business/inbox"
            className="text-sm text-brand hover:text-brand-dark"
          >
            Open inbox →
          </Link>
        </div>
        {pending.length === 0 ? (
          <p className="p-6 text-sm text-slate-500">
            Nothing pending right now.
          </p>
        ) : (
          <ul className="divide-y divide-slate-100">
            {pending.map((p) => (
              <li
                key={`${p.union}-${p.period}`}
                className="flex items-center justify-between px-5 py-3 text-sm"
              >
                <div>
                  <span className="font-medium text-slate-900">{p.union}</span>
                  <span className="ml-2 text-slate-500">· {p.period}</span>
                </div>
                {(p.gap_count ?? 0) > 0 && (
                  <span className="rounded-full bg-rose-50 px-2 py-1 text-xs text-rose-700">
                    {p.gap_count} gap{p.gap_count === 1 ? "" : "s"}
                  </span>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
