import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
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
      return "approved the rate sheet";
    case "reject":
      return `rejected — "${String(d.reason ?? "")}"${
        Array.isArray(d.tags) && d.tags.length ? ` [${d.tags.join(", ")}]` : ""
      }`;
    case "comment":
      return `commented: "${String(d.text ?? "")}"`;
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

// Slug shape used by the Business routes: /business/Sprinkler+704/2026-01-01
function sheetSlug(local: unknown, period: unknown): string | null {
  const l = local == null ? "" : String(local);
  const p = period == null ? "" : String(period);
  if (!l || !p) return null;
  // We don't know the trade from the audit row, so fall back to a numeric-only
  // union slug — the RateSheetReview parser strips everything except the
  // trailing digits, so this round-trips fine.
  return `/business/rate-sheets/${encodeURIComponent(l)}/${encodeURIComponent(p)}`;
}

function actionCount(records: AuditRecord[], action: string): number {
  return records.filter((r) => r.action === action).length;
}

export function Me(): JSX.Element {
  const [records, setRecords] = useState<AuditRecord[]>([]);
  const [scope, setScope] = useState<"me" | "all" | "">("");
  const [filter, setFilter] = useState<string>("all");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = useCallback(() => {
    setLoading(true);
    setError("");
    api
      .get<{ scope: "me" | "all"; records: AuditRecord[]; count: number }>(
        "/v1/audit?scope=me",
      )
      .then((r) => {
        setRecords(r.records ?? []);
        setScope(r.scope);
      })
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false));
  }, []);

  useEffect(load, [load]);

  const filtered = useMemo(() => {
    if (filter === "all") return records;
    return records.filter((r) => r.action === filter);
  }, [filter, records]);

  // Group by {local, period} so the user sees activity per sheet, then
  // chronological within each sheet.
  const grouped = useMemo(() => {
    const m = new Map<string, AuditRecord[]>();
    for (const r of filtered) {
      const local = r.details?.local ?? "?";
      const period = r.details?.period ?? "?";
      const key = `${local}|${period}`;
      const cur = m.get(key) ?? [];
      cur.push(r);
      m.set(key, cur);
    }
    return Array.from(m.entries()).map(([key, rs]) => {
      const [local, period] = key.split("|");
      return { local, period, records: rs };
    });
  }, [filtered]);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold text-slate-900">My Activity</h1>
          <p className="mt-0.5 text-sm text-slate-500">
            {scope === "me"
              ? "Your approvals, rejections, comments, and overrides — newest first."
              : scope === "all"
                ? "Global audit log (admin view) — newest first."
                : ""}
          </p>
        </div>
        <button
          type="button"
          onClick={load}
          className="rounded-md border border-slate-200 px-3 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50"
        >
          Refresh
        </button>
      </div>

      <div className="flex flex-wrap items-center gap-3 rounded-lg border border-slate-200 bg-white p-3 shadow-sm">
        <span className="text-xs font-medium uppercase tracking-wide text-slate-500">
          Totals
        </span>
        {(["approve", "reject", "comment", "override"] as const).map((k) => (
          <span
            key={k}
            className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${
              ACTION_TONE[k] ?? "bg-slate-100 text-slate-700 ring-slate-200"
            }`}
          >
            {k} <span className="font-mono tabular-nums">{actionCount(records, k)}</span>
          </span>
        ))}
        <div className="ml-auto flex items-center gap-1 text-xs">
          <span className="text-slate-500">Filter:</span>
          {(["all", "approve", "reject", "comment", "override"] as const).map((k) => (
            <button
              key={k}
              type="button"
              onClick={() => setFilter(k)}
              className={`rounded-full px-2 py-0.5 ring-1 ring-inset transition ${
                filter === k
                  ? "bg-slate-900 text-white ring-slate-900"
                  : "bg-white text-slate-600 ring-slate-200 hover:bg-slate-50"
              }`}
            >
              {k}
            </button>
          ))}
        </div>
      </div>

      {error && (
        <p className="rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-700">
          {error}
        </p>
      )}

      {loading && records.length === 0 ? (
        <p className="rounded-lg border border-slate-200 bg-white p-6 text-center text-sm text-slate-500 shadow-sm">
          Loading…
        </p>
      ) : grouped.length === 0 ? (
        <p className="rounded-lg border border-slate-200 bg-white p-6 text-center text-sm text-slate-500 shadow-sm">
          Nothing yet. Approve / reject / comment / override actions will appear here.
        </p>
      ) : (
        grouped.map(({ local, period, records: rs }) => {
          const slug = sheetSlug(local, period);
          return (
            <div
              key={`${local}|${period}`}
              className="rounded-lg border border-slate-200 bg-white shadow-sm"
            >
              <div className="flex items-center justify-between border-b border-slate-100 px-5 py-3">
                <div>
                  <span className="text-xs font-medium uppercase tracking-wide text-slate-500">
                    Union {local} · {period}
                  </span>
                  <span className="ml-2 text-xs text-slate-400">
                    ({rs.length} action{rs.length === 1 ? "" : "s"})
                  </span>
                </div>
                {slug && (
                  <Link to={slug} className="text-xs text-brand hover:text-brand-dark">
                    Open rate sheet ↗
                  </Link>
                )}
              </div>
              <ul className="divide-y divide-slate-100">
                {rs.map((r) => (
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
            </div>
          );
        })
      )}
    </div>
  );
}
