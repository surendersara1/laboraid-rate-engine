import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../lib/api";
import { usePolling } from "../lib/usePolling";
import type { Job } from "../types/api";

const STATUS_PILL: Record<string, string> = {
  SUCCEEDED: "bg-emerald-100 text-emerald-800 ring-emerald-200",
  RUNNING: "bg-sky-100 text-sky-800 ring-sky-200",
  FAILED: "bg-rose-100 text-rose-800 ring-rose-200",
  TIMED_OUT: "bg-rose-100 text-rose-800 ring-rose-200",
  ABORTED: "bg-slate-100 text-slate-700 ring-slate-200",
};

function fmtDuration(ms?: number | null): string {
  if (ms == null) return "—";
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`;
  const m = Math.floor(ms / 60_000);
  const s = Math.round((ms - m * 60_000) / 1000);
  return `${m}m ${s}s`;
}

function fmtTime(iso?: string): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString();
}

export function Jobs(): JSX.Element {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  const load = useCallback(() => {
    api
      .get<{ jobs: Job[] }>("/v1/jobs")
      .then((r) => setJobs(r.jobs ?? []))
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false));
  }, []);

  useEffect(load, [load]);
  const anyRunning = jobs.some((j) => j.status === "RUNNING");
  usePolling(load, anyRunning);

  if (error) {
    return (
      <div className="rounded-md border border-rose-200 bg-rose-50 p-3 text-sm text-rose-700">
        {error}
      </div>
    );
  }

  const counts = {
    total: jobs.length,
    succeeded: jobs.filter((j) => j.status === "SUCCEEDED").length,
    running: jobs.filter((j) => j.status === "RUNNING").length,
    failed: jobs.filter((j) => j.status === "FAILED" || j.status === "TIMED_OUT").length,
  };

  return (
    <div className="space-y-5">
      <div className="flex items-end justify-between">
        <div>
          <h2 className="text-2xl font-semibold text-slate-900">Jobs</h2>
          <p className="text-sm text-slate-500">
            Every PDF upload that triggered a pipeline run.
          </p>
        </div>
        <div className="flex gap-2 text-xs text-slate-600">
          <span>
            <span className="font-semibold text-slate-900">{counts.total}</span> total
          </span>
          <span className="text-slate-300">·</span>
          <span>
            <span className="font-semibold text-emerald-600">{counts.succeeded}</span>{" "}
            ok
          </span>
          <span className="text-slate-300">·</span>
          <span>
            <span className="font-semibold text-sky-600">{counts.running}</span> running
          </span>
          <span className="text-slate-300">·</span>
          <span>
            <span className="font-semibold text-rose-600">{counts.failed}</span> failed
          </span>
        </div>
      </div>

      {loading ? (
        <p className="text-slate-500">Loading…</p>
      ) : jobs.length === 0 ? (
        <div className="rounded-lg border border-slate-200 bg-white p-8 text-center text-slate-500">
          No jobs yet.
        </div>
      ) : (
        <div className="overflow-hidden rounded-lg border border-slate-200 bg-white shadow-sm">
          <table className="w-full text-sm">
            <thead className="bg-slate-50 text-left text-xs font-medium uppercase tracking-wide text-slate-500">
              <tr>
                <th className="px-4 py-3">Status</th>
                <th className="px-4 py-3">Union</th>
                <th className="px-4 py-3">Period</th>
                <th className="px-4 py-3">Started</th>
                <th className="px-4 py-3 text-right">Duration</th>
                <th className="px-4 py-3">Job ID</th>
                <th className="px-4 py-3"></th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {jobs.map((j) => (
                <tr key={j.job_id} className="hover:bg-slate-50">
                  <td className="px-4 py-3">
                    <span
                      className={`inline-flex items-center rounded-full px-2 py-1 text-xs font-medium ring-1 ring-inset ${
                        STATUS_PILL[j.status] ?? "bg-slate-100 text-slate-700"
                      }`}
                    >
                      {j.status}
                    </span>
                  </td>
                  <td className="px-4 py-3 font-medium text-slate-900">
                    {j.union || "—"}
                  </td>
                  <td className="px-4 py-3 text-slate-700">{j.period || "—"}</td>
                  <td className="px-4 py-3 text-slate-600">{fmtTime(j.started_at)}</td>
                  <td className="px-4 py-3 text-right font-mono tabular-nums text-slate-700">
                    {fmtDuration(j.duration_ms)}
                  </td>
                  <td className="px-4 py-3 font-mono text-xs text-slate-500">
                    {j.job_id.slice(0, 16)}…
                  </td>
                  <td className="px-4 py-3 text-right">
                    <Link
                      to={`/admin/jobs/${encodeURIComponent(j.job_id)}`}
                      className="text-brand hover:text-brand-dark"
                    >
                      Detail →
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
