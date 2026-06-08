import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { api } from "../lib/api";
import type { Job, RateSheetSummary } from "../types/api";

const STATUS_COLOR: Record<string, string> = {
  SUCCEEDED: "#10b981",
  RUNNING: "#0ea5e9",
  FAILED: "#f43f5e",
  TIMED_OUT: "#fb923c",
  ABORTED: "#94a3b8",
};

function fmtDuration(ms?: number | null): string {
  if (ms == null) return "—";
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`;
  const m = Math.floor(ms / 60_000);
  const s = Math.round((ms - m * 60_000) / 1000);
  return `${m}m ${s}s`;
}

export function Dashboard(): JSX.Element {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [pending, setPending] = useState<RateSheetSummary[]>([]);

  useEffect(() => {
    Promise.all([
      api.get<{ jobs: Job[] }>("/v1/jobs").catch(() => ({ jobs: [] })),
      api
        .get<{ records: RateSheetSummary[] }>(
          "/v1/unions/all/rate-sheets?approval_state=pending_review",
        )
        .catch(() => ({ records: [] })),
    ]).then(([j, p]) => {
      setJobs(j.jobs ?? []);
      setPending(p.records ?? []);
    });
  }, []);

  const totalJobs = jobs.length;
  const succeeded = jobs.filter((j) => j.status === "SUCCEEDED").length;
  const failed = jobs.filter(
    (j) => j.status === "FAILED" || j.status === "TIMED_OUT",
  ).length;
  const running = jobs.filter((j) => j.status === "RUNNING").length;
  const avgDuration =
    jobs.length === 0
      ? null
      : Math.round(
          jobs
            .filter((j) => j.duration_ms != null)
            .reduce((s, j) => s + (j.duration_ms || 0), 0) /
            Math.max(
              1,
              jobs.filter((j) => j.duration_ms != null).length,
            ),
        );

  // Charts data
  const statusPie = [
    { name: "Succeeded", value: succeeded, color: STATUS_COLOR.SUCCEEDED },
    { name: "Running", value: running, color: STATUS_COLOR.RUNNING },
    { name: "Failed", value: failed, color: STATUS_COLOR.FAILED },
    {
      name: "Other",
      value: totalJobs - succeeded - running - failed,
      color: STATUS_COLOR.ABORTED,
    },
  ].filter((s) => s.value > 0);

  const recent = [...jobs]
    .sort((a, b) =>
      (b.started_at ?? "").localeCompare(a.started_at ?? ""),
    )
    .slice(0, 10);
  const durChart = recent
    .filter((j) => j.duration_ms != null)
    .reverse()
    .map((j) => ({
      label: (j.union || j.job_id.slice(0, 8)) + " · " + (j.period || ""),
      ms: j.duration_ms || 0,
      fill: STATUS_COLOR[j.status] ?? "#94a3b8",
    }));

  const tiles: Array<[string, string | number, string]> = [
    ["Total runs", totalJobs, "bg-slate-100 text-slate-700"],
    ["Succeeded", succeeded, "bg-emerald-100 text-emerald-800"],
    ["Failed", failed, "bg-rose-100 text-rose-800"],
    ["Pending review", pending.length, "bg-amber-100 text-amber-800"],
    ["Avg duration", fmtDuration(avgDuration), "bg-sky-100 text-sky-800"],
  ];

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-2xl font-semibold text-slate-900">Dashboard</h2>
        <p className="text-sm text-slate-500">
          LaborAid Rate Engine · pipeline health at a glance
        </p>
      </div>

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-5">
        {tiles.map(([label, value, pill]) => (
          <div
            key={label}
            className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm"
          >
            <p className="text-xs font-medium uppercase tracking-wide text-slate-500">
              {label}
            </p>
            <p className="mt-2 text-2xl font-semibold text-slate-900">{value}</p>
            <span
              className={`mt-2 inline-block rounded-full px-2 py-0.5 text-xs ${pill}`}
            >
              live
            </span>
          </div>
        ))}
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
        <div className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm">
          <h3 className="mb-3 text-sm font-semibold uppercase tracking-wide text-slate-500">
            Status mix
          </h3>
          {statusPie.length === 0 ? (
            <p className="text-sm text-slate-500">No runs yet.</p>
          ) : (
            <ResponsiveContainer width="100%" height={220}>
              <PieChart>
                <Pie
                  data={statusPie}
                  dataKey="value"
                  nameKey="name"
                  innerRadius={55}
                  outerRadius={85}
                  paddingAngle={2}
                >
                  {statusPie.map((s) => (
                    <Cell key={s.name} fill={s.color} />
                  ))}
                </Pie>
                <Tooltip
                  contentStyle={{
                    borderRadius: 6,
                    border: "1px solid #e2e8f0",
                    fontSize: 12,
                  }}
                />
              </PieChart>
            </ResponsiveContainer>
          )}
          <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-xs">
            {statusPie.map((s) => (
              <span key={s.name} className="flex items-center gap-1 text-slate-600">
                <span
                  className="inline-block h-2 w-2 rounded-full"
                  style={{ backgroundColor: s.color }}
                />
                {s.name} · {s.value}
              </span>
            ))}
          </div>
        </div>

        <div className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm lg:col-span-2">
          <h3 className="mb-3 text-sm font-semibold uppercase tracking-wide text-slate-500">
            Duration of recent runs
          </h3>
          {durChart.length === 0 ? (
            <p className="text-sm text-slate-500">No completed runs.</p>
          ) : (
            <ResponsiveContainer width="100%" height={220}>
              <BarChart
                data={durChart}
                margin={{ top: 10, right: 10, left: 0, bottom: 50 }}
              >
                <CartesianGrid
                  strokeDasharray="3 3"
                  stroke="#e2e8f0"
                  vertical={false}
                />
                <XAxis
                  dataKey="label"
                  tick={{ fontSize: 10, fill: "#64748b" }}
                  interval={0}
                  angle={-30}
                  textAnchor="end"
                />
                <YAxis
                  tick={{ fontSize: 11, fill: "#64748b" }}
                  tickFormatter={(v) => fmtDuration(v)}
                  width={70}
                />
                <Tooltip
                  formatter={(v: number) => fmtDuration(v)}
                  contentStyle={{
                    borderRadius: 6,
                    border: "1px solid #e2e8f0",
                    fontSize: 12,
                  }}
                />
                <Bar dataKey="ms" radius={[4, 4, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          )}
        </div>
      </div>

      <div className="rounded-lg border border-slate-200 bg-white shadow-sm">
        <div className="flex items-center justify-between border-b border-slate-100 px-5 py-3">
          <h3 className="text-sm font-semibold uppercase tracking-wide text-slate-500">
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
          <p className="p-6 text-sm text-slate-500">Nothing pending right now.</p>
        ) : (
          <ul className="divide-y divide-slate-100">
            {pending.slice(0, 5).map((p) => (
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
