import { useCallback, useEffect, useState } from "react";
import { api } from "../lib/api";

interface AuditRecord {
  id: number | string;
  ts: string;
  actor: string;
  action: string;
  details: Record<string, unknown>;
}

const ACTION_TONE: Record<string, string> = {
  approve: "bg-emerald-100 text-emerald-800 ring-emerald-200",
  reject: "bg-rose-100 text-rose-800 ring-rose-200",
  comment: "bg-sky-100 text-sky-800 ring-sky-200",
  override: "bg-amber-100 text-amber-800 ring-amber-200",
  publish: "bg-indigo-100 text-indigo-800 ring-indigo-200",
};

function fmtTime(iso: string): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString();
}

function relTime(iso: string): string {
  if (!iso) return "";
  const ms = Date.now() - new Date(iso).getTime();
  if (ms < 60_000) return "just now";
  if (ms < 3_600_000) return `${Math.floor(ms / 60_000)}m ago`;
  if (ms < 86_400_000) return `${Math.floor(ms / 3_600_000)}h ago`;
  return `${Math.floor(ms / 86_400_000)}d ago`;
}

function describe(rec: AuditRecord): string {
  const d = rec.details || {};
  switch (rec.action) {
    case "approve":
      return `approved the rate sheet`;
    case "reject":
      return `rejected — “${String(d.reason ?? "")}”${
        Array.isArray(d.tags) && d.tags.length ? ` [${d.tags.join(", ")}]` : ""
      }`;
    case "comment":
      return `commented: “${String(d.text ?? "")}”`;
    case "override":
      return `overrode ${String(d.package ?? "")} · ${String(
        d.column_name ?? "",
      )}: ${String(d.old_value ?? "")} → ${String(d.new_value ?? "")}${
        d.justification ? ` (${String(d.justification)})` : ""
      }`;
    default:
      return rec.action;
  }
}

export function ActivityTimeline({
  local,
  period,
  refreshKey,
}: {
  local: string;
  period: string;
  refreshKey?: number;
}): JSX.Element {
  const [records, setRecords] = useState<AuditRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = useCallback(() => {
    setLoading(true);
    api
      .get<{ records: AuditRecord[]; count: number }>(
        `/v1/unions/${local}/rate-sheets/${period}/audit`,
      )
      .then((r) => setRecords(r.records ?? []))
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false));
  }, [local, period]);

  useEffect(load, [load, refreshKey]);

  return (
    <div className="rounded-lg border border-slate-200 bg-white shadow-sm">
      <div className="flex items-center justify-between border-b border-slate-100 px-5 py-3">
        <h3 className="text-sm font-semibold uppercase tracking-wide text-slate-500">
          Activity
        </h3>
        <button
          type="button"
          onClick={load}
          className="text-xs text-brand hover:text-brand-dark"
        >
          Refresh
        </button>
      </div>
      {error ? (
        <p className="px-5 py-4 text-sm text-rose-600">{error}</p>
      ) : loading && records.length === 0 ? (
        <p className="px-5 py-4 text-sm text-slate-500">Loading…</p>
      ) : records.length === 0 ? (
        <p className="px-5 py-4 text-sm text-slate-500">
          Nothing yet. Approve / reject / comment / override actions will show
          up here.
        </p>
      ) : (
        <ul className="divide-y divide-slate-100">
          {records.map((r) => (
            <li key={r.id} className="flex items-start gap-3 px-5 py-3 text-sm">
              <span
                className={`mt-0.5 inline-flex shrink-0 items-center rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${
                  ACTION_TONE[r.action] ?? "bg-slate-100 text-slate-700 ring-slate-200"
                }`}
              >
                {r.action}
              </span>
              <div className="min-w-0 flex-1">
                <div className="text-slate-800">
                  <span className="font-medium">{r.actor}</span> {describe(r)}
                </div>
                <div className="mt-0.5 text-xs text-slate-500" title={fmtTime(r.ts)}>
                  {relTime(r.ts)} · {fmtTime(r.ts)}
                </div>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
