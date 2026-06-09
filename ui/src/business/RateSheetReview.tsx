import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ApproveRejectBar } from "../components/ApproveRejectBar";
import { CellOverrideModal } from "../components/CellOverrideModal";
import { PdfViewer } from "../components/PdfViewer";
import { ProvenancePanel } from "../components/ProvenancePanel";
import { RateCellTable } from "../components/RateCellTable";
import { api } from "../lib/api";
import type { RateCell, RateSheetDetail } from "../types/api";

const APPROVAL_PILL: Record<string, string> = {
  pending_review: "bg-amber-100 text-amber-800 ring-amber-200",
  approved: "bg-emerald-100 text-emerald-800 ring-emerald-200",
  rejected: "bg-rose-100 text-rose-800 ring-rose-200",
  published: "bg-indigo-100 text-indigo-800 ring-indigo-200",
};

const JOB_PILL: Record<string, string> = {
  SUCCEEDED: "bg-emerald-100 text-emerald-800 ring-emerald-200",
  RUNNING: "bg-sky-100 text-sky-800 ring-sky-200",
  FAILED: "bg-rose-100 text-rose-800 ring-rose-200",
  TIMED_OUT: "bg-rose-100 text-rose-800 ring-rose-200",
  ABORTED: "bg-slate-100 text-slate-700 ring-slate-200",
};

function unionLocal(display: string): string {
  const m = display.match(/(\d{2,4})\s*$/);
  return m ? m[1] : display;
}

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

export function RateSheetReview(): JSX.Element {
  const { union = "", period = "" } = useParams();
  const [selected, setSelected] = useState<RateCell | null>(null);
  const [detail, setDetail] = useState<RateSheetDetail | null>(null);
  const [state, setState] = useState("pending_review");
  const [error, setError] = useState("");

  useEffect(() => {
    setSelected(null);
    setError("");
    const local = unionLocal(union);
    api
      .get<RateSheetDetail>(`/v1/unions/${local}/rate-sheets/${period}`)
      .then((r) => {
        setDetail(r);
        setState(r.approval_state ?? "pending_review");
      })
      .catch((e) => setError(String(e)));
  }, [union, period]);

  const cells = detail?.cells ?? [];
  const artifacts = detail?.artifacts ?? [];
  const job = detail?.job_meta;
  const counts = detail?.counts ?? {};
  const reviewQueueEmpty = cells.length === 0;

  return (
    <div className="space-y-4">
      {error && (
        <p className="rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-700">
          {error}
        </p>
      )}

      {/* HEADER CARD */}
      <div className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h2 className="text-2xl font-semibold text-slate-900">
              {detail?.union || union} · {period}
            </h2>
            <p className="mt-1 text-sm text-slate-500">
              {job?.started_at ? (
                <>
                  Extracted{" "}
                  <span className="text-slate-700">{fmtTime(job.started_at)}</span>{" "}
                  by ExtractorAgent ·{" "}
                  <span className="text-slate-700">{fmtDuration(job.duration_ms)}</span> ·{" "}
                  <Link
                    to={`/admin/jobs/${encodeURIComponent(job.job_id)}`}
                    className="font-mono text-brand hover:text-brand-dark"
                  >
                    job {job.job_id.slice(0, 8)}… ↗
                  </Link>
                </>
              ) : (
                <span>Extraction job metadata not available</span>
              )}
            </p>
            <p className="mt-2 flex gap-3 text-xs text-slate-600">
              <span>
                <span className="font-semibold text-slate-900">
                  {counts.classifications ?? "—"}
                </span>{" "}
                classifications
              </span>
              <span className="text-slate-300">·</span>
              <span>
                <span className="font-semibold text-slate-900">
                  {counts.cells ?? cells.length}
                </span>{" "}
                cells
              </span>
              <span className="text-slate-300">·</span>
              <span>
                <span className="font-semibold text-slate-900">
                  {counts.gaps ?? 0}
                </span>{" "}
                gap{(counts.gaps ?? 0) === 1 ? "" : "s"}
              </span>
            </p>
          </div>
          <div className="flex flex-col items-end gap-1">
            <span
              className={`inline-flex items-center rounded-full px-3 py-1 text-xs font-medium ring-1 ring-inset ${
                APPROVAL_PILL[state] ?? "bg-slate-100 text-slate-700 ring-slate-200"
              }`}
            >
              {state.replace("_", " ")}
            </span>
            {job?.status && (
              <span
                className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${
                  JOB_PILL[job.status] ?? "bg-slate-100 text-slate-700 ring-slate-200"
                }`}
              >
                pipeline {job.status.toLowerCase()}
              </span>
            )}
          </div>
        </div>
      </div>

      {/* ACTION BAR */}
      <ApproveRejectBar
        union={union}
        period={period}
        approvalState={state}
        reviewQueueEmpty={reviewQueueEmpty}
        onChanged={setState}
      />

      {/* 3-PANE BODY */}
      <div className="grid grid-cols-12 gap-3" style={{ minHeight: 600 }}>
        <div className="col-span-4 h-[640px]">
          <PdfViewer url={detail?.source_pdf_url ?? ""} />
        </div>
        <div className="col-span-6 h-[640px] overflow-auto rounded-md border border-slate-200 bg-white shadow-sm">
          <RateCellTable cells={cells} onSelect={setSelected} />
        </div>
        <div className="col-span-2 h-[640px] overflow-auto rounded-md border border-slate-200 bg-white shadow-sm">
          <ProvenancePanel cell={selected} />
        </div>
      </div>

      {/* ARTIFACTS PANEL */}
      <div className="rounded-lg border border-slate-200 bg-white shadow-sm">
        <div className="flex items-center justify-between border-b border-slate-100 px-5 py-3">
          <h3 className="text-sm font-semibold uppercase tracking-wide text-slate-500">
            Artifacts
          </h3>
          <span className="text-xs text-slate-500">
            {artifacts.filter((a) => a.url).length} of {artifacts.length} produced
          </span>
        </div>
        <ul className="divide-y divide-slate-100">
          {artifacts.map((a) => (
            <li
              key={`${a.bucket}/${a.key || a.name}`}
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
                {a.key && (
                  <p className="mt-0.5 truncate font-mono text-xs text-slate-500">
                    s3://{a.bucket}/{a.key}
                  </p>
                )}
              </div>
              <div className="flex items-center gap-3 text-xs text-slate-500">
                <span className="font-mono tabular-nums">{fmtBytes(a.size)}</span>
                {a.url ? (
                  <a
                    href={a.url}
                    target="_blank"
                    rel="noreferrer"
                    className="font-medium text-brand hover:text-brand-dark"
                  >
                    Open ↗
                  </a>
                ) : (
                  <span className="text-slate-400">not produced</span>
                )}
              </div>
            </li>
          ))}
          {job && (
            <li className="flex items-center justify-between px-5 py-3 text-sm">
              <div>
                <div className="flex items-center gap-2">
                  <span className="font-medium text-slate-900">
                    Step Functions trace
                  </span>
                  <span className="rounded bg-slate-100 px-1.5 py-0.5 text-xs text-slate-700">
                    pipeline
                  </span>
                </div>
                <p className="mt-0.5 font-mono text-xs text-slate-500">
                  job {job.job_id}
                </p>
              </div>
              <Link
                to={`/admin/jobs/${encodeURIComponent(job.job_id)}`}
                className="text-xs font-medium text-brand hover:text-brand-dark"
              >
                Open in Admin ↗
              </Link>
            </li>
          )}
        </ul>
      </div>

      {/* ACTIVITY — Tier 2 work; render a placeholder so the layout is final */}
      <div className="rounded-lg border border-slate-200 bg-white shadow-sm">
        <div className="border-b border-slate-100 px-5 py-3">
          <h3 className="text-sm font-semibold uppercase tracking-wide text-slate-500">
            Activity
          </h3>
        </div>
        <p className="px-5 py-4 text-sm text-slate-500">
          Approvals, rejections, comments, and overrides will be listed here
          (Tier 2 work).
        </p>
      </div>

      <CellOverrideModal />
    </div>
  );
}
