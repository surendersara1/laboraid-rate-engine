import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { api } from "../lib/api";
import type { JobDetail as JobDetailT } from "../types/api";

const STATUS_PILL: Record<string, string> = {
  SUCCEEDED: "bg-emerald-100 text-emerald-800 ring-emerald-200",
  RUNNING: "bg-sky-100 text-sky-800 ring-sky-200",
  FAILED: "bg-rose-100 text-rose-800 ring-rose-200",
  TIMED_OUT: "bg-rose-100 text-rose-800 ring-rose-200",
  ABORTED: "bg-slate-100 text-slate-700 ring-slate-200",
};

const STEP_DOT: Record<string, string> = {
  ok: "bg-emerald-500",
  failed: "bg-rose-500",
  running: "bg-sky-500 animate-pulse",
};

function fmtDuration(ms?: number | null): string {
  if (ms == null) return "—";
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`;
  const m = Math.floor(ms / 60_000);
  const s = Math.round((ms - m * 60_000) / 1000);
  return `${m}m ${s}s`;
}

function fmtBytes(n?: number | null): string {
  if (n == null) return "—";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(2)} MB`;
}

function fmtTime(iso?: string): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString();
}

export function JobDetail(): JSX.Element {
  const { id = "" } = useParams();
  const [job, setJob] = useState<JobDetailT | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    api
      .get<JobDetailT>(`/v1/jobs/${encodeURIComponent(id)}`)
      .then(setJob)
      .catch((e) => setError(String(e)));
  }, [id]);

  if (error) {
    return (
      <div className="rounded-md border border-rose-200 bg-rose-50 p-3 text-sm text-rose-700">
        {error}
      </div>
    );
  }
  if (!job) {
    return <p className="text-slate-500">Loading…</p>;
  }

  const cwUrl = job.agent_log_group
    ? `https://us-east-2.console.aws.amazon.com/cloudwatch/home?region=us-east-2#logsV2:log-groups/log-group/${encodeURIComponent(
        job.agent_log_group,
      )}`
    : undefined;

  const chartData = job.timeline.map((s) => ({
    name: s.name,
    ms: s.duration_ms ?? 0,
    fill:
      s.status === "failed"
        ? "#f43f5e"
        : s.status === "running"
          ? "#0ea5e9"
          : "#10b981",
  }));

  return (
    <div className="space-y-6">
      {/* Header */}
      <div>
        <div className="flex items-start justify-between">
          <div>
            <h2 className="text-2xl font-semibold text-slate-900">
              {job.union || "Job"} · {job.period || "—"}
            </h2>
            <p className="mt-1 font-mono text-xs text-slate-500">{job.job_id}</p>
          </div>
          <span
            className={`inline-flex items-center rounded-full px-3 py-1 text-sm font-medium ring-1 ring-inset ${
              STATUS_PILL[job.status] ?? "bg-slate-100 text-slate-700"
            }`}
          >
            {job.status}
          </span>
        </div>
      </div>

      {/* Stat cards */}
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <div className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
          <p className="text-xs font-medium uppercase tracking-wide text-slate-500">
            Duration
          </p>
          <p className="mt-1 text-2xl font-semibold text-slate-900">
            {fmtDuration(job.duration_ms)}
          </p>
        </div>
        <div className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
          <p className="text-xs font-medium uppercase tracking-wide text-slate-500">
            Started
          </p>
          <p className="mt-1 text-sm text-slate-700">{fmtTime(job.started_at)}</p>
        </div>
        <div className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
          <p className="text-xs font-medium uppercase tracking-wide text-slate-500">
            Stages
          </p>
          <p className="mt-1 text-2xl font-semibold text-slate-900">
            {job.timeline.filter((t) => t.status === "ok").length}/
            {job.timeline.length}
          </p>
        </div>
        <div className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
          <p className="text-xs font-medium uppercase tracking-wide text-slate-500">
            Artifacts
          </p>
          <p className="mt-1 text-2xl font-semibold text-slate-900">
            {job.artifacts.filter((a) => a.url).length}/{job.artifacts.length}
          </p>
        </div>
      </div>

      {/* Timeline + chart */}
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <div className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm">
          <h3 className="mb-4 text-sm font-semibold uppercase tracking-wide text-slate-500">
            Pipeline timeline
          </h3>
          <ol className="space-y-3">
            {job.timeline.map((step, i) => (
              <li key={step.name} className="flex gap-3">
                <div className="flex flex-col items-center">
                  <div
                    className={`mt-1 h-3 w-3 rounded-full ${
                      STEP_DOT[step.status] ?? "bg-slate-300"
                    }`}
                  />
                  {i < job.timeline.length - 1 && (
                    <div className="my-1 w-px flex-1 bg-slate-200" />
                  )}
                </div>
                <div className="flex-1 pb-3">
                  <div className="flex items-center justify-between">
                    <span className="font-medium text-slate-900">{step.name}</span>
                    <span className="font-mono text-xs tabular-nums text-slate-500">
                      {fmtDuration(step.duration_ms)}
                    </span>
                  </div>
                  {step.error && (
                    <p className="mt-1 text-xs text-rose-600">
                      <span className="font-semibold">{step.error}</span>
                      {step.cause ? ` · ${step.cause}` : ""}
                    </p>
                  )}
                </div>
              </li>
            ))}
          </ol>
        </div>

        <div className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm">
          <h3 className="mb-4 text-sm font-semibold uppercase tracking-wide text-slate-500">
            Duration per stage
          </h3>
          {chartData.length > 0 ? (
            <ResponsiveContainer width="100%" height={260}>
              <BarChart
                data={chartData}
                margin={{ top: 10, right: 10, left: 0, bottom: 32 }}
              >
                <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" vertical={false} />
                <XAxis
                  dataKey="name"
                  tick={{ fontSize: 11, fill: "#64748b" }}
                  interval={0}
                  angle={-25}
                  textAnchor="end"
                />
                <YAxis
                  tick={{ fontSize: 11, fill: "#64748b" }}
                  tickFormatter={(v) => fmtDuration(v)}
                  width={70}
                />
                <Tooltip
                  formatter={(v) => fmtDuration(typeof v === "number" ? v : 0)}
                  contentStyle={{
                    borderRadius: 6,
                    border: "1px solid #e2e8f0",
                    fontSize: 12,
                  }}
                />
                <Bar dataKey="ms" radius={[4, 4, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          ) : (
            <p className="text-sm text-slate-500">No stages recorded yet.</p>
          )}
        </div>
      </div>

      {/* Artifacts */}
      <div className="rounded-lg border border-slate-200 bg-white shadow-sm">
        <div className="border-b border-slate-100 px-5 py-3">
          <h3 className="text-sm font-semibold uppercase tracking-wide text-slate-500">
            Artifacts
          </h3>
        </div>
        <ul className="divide-y divide-slate-100">
          {job.artifacts.length === 0 ? (
            <li className="p-5 text-sm text-slate-500">No artifacts.</li>
          ) : (
            job.artifacts.map((a) => (
              <li
                key={a.key}
                className="flex items-center justify-between px-5 py-3 text-sm"
              >
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span className="font-medium text-slate-900">{a.name}</span>
                    <span
                      className={`rounded px-1.5 py-0.5 text-xs ${
                        a.kind === "input"
                          ? "bg-slate-100 text-slate-700"
                          : "bg-indigo-50 text-indigo-700"
                      }`}
                    >
                      {a.kind}
                    </span>
                  </div>
                  <p className="mt-0.5 truncate font-mono text-xs text-slate-500">
                    s3://{a.bucket}/{a.key}
                  </p>
                </div>
                <div className="flex items-center gap-3 text-xs text-slate-500">
                  <span className="font-mono tabular-nums">{fmtBytes(a.size)}</span>
                  {a.url ? (
                    <a
                      href={a.url}
                      target="_blank"
                      rel="noreferrer"
                      className="text-brand hover:text-brand-dark"
                    >
                      Open ↗
                    </a>
                  ) : (
                    <span className="text-slate-400">not produced</span>
                  )}
                </div>
              </li>
            ))
          )}
        </ul>
      </div>

      {/* Logs deep-link */}
      {cwUrl && (
        <div className="rounded-lg border border-slate-200 bg-slate-50 p-4 text-sm">
          <span className="text-slate-600">Agent runtime logs are in CloudWatch:</span>{" "}
          <a
            href={cwUrl}
            target="_blank"
            rel="noreferrer"
            className="font-medium text-brand hover:text-brand-dark"
          >
            Open in AWS Console ↗
          </a>
          <p className="mt-1 font-mono text-xs text-slate-500">
            {job.agent_log_group}
          </p>
        </div>
      )}
    </div>
  );
}
