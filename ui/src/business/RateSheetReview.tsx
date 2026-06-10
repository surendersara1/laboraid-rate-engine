import { useCallback, useEffect, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { ActivityTimeline } from "../components/ActivityTimeline";
import { ApproveRejectBar } from "../components/ApproveRejectBar";
import { CellCommentModal } from "../components/CellCommentModal";
import { CellOverrideModal } from "../components/CellOverrideModal";
import { ProvenancePanel } from "../components/ProvenancePanel";
import { RateCellTable } from "../components/RateCellTable";
import { ReworkBar } from "../components/ReworkBar";
import { api } from "../lib/api";
import type { JobArtifact, RateCell, RateSheetDetail } from "../types/api";

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

function ArtifactCard({ a }: { a: JobArtifact }): JSX.Element {
  const filename = a.key ? a.key.split("/").pop() ?? a.name : a.name;
  const available = Boolean(a.url);
  const kindLabel =
    a.kind === "input" ? "Source" : a.kind === "output" ? "Output" : "Artifact";

  return (
    <a
      href={a.url ?? undefined}
      target={available ? "_blank" : undefined}
      rel="noreferrer"
      onClick={(e) => {
        if (!available) e.preventDefault();
      }}
      className={`block rounded-lg border bg-white p-4 shadow-sm transition ${
        available
          ? "border-slate-200 hover:border-brand hover:shadow"
          : "cursor-not-allowed border-slate-200 opacity-60"
      }`}
      title={available ? "Open in a new tab" : "Not produced yet"}
    >
      <div className="flex items-start justify-between">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="text-xs font-medium uppercase tracking-wide text-slate-500">
              {kindLabel}
            </span>
            <span className="text-xs text-slate-300">·</span>
            <span className="font-mono text-xs tabular-nums text-slate-500">
              {fmtBytes(a.size)}
            </span>
          </div>
          <div className="mt-1 font-medium text-slate-900">{a.name}</div>
          <div className="mt-0.5 truncate font-mono text-xs text-slate-500">
            {filename}
          </div>
        </div>
        <span
          className={`ml-2 mt-1 text-sm font-medium ${
            available ? "text-brand" : "text-slate-300"
          }`}
        >
          {available ? "Open ↗" : "—"}
        </span>
      </div>
    </a>
  );
}

export function RateSheetReview(): JSX.Element {
  const { union = "", period = "" } = useParams();
  const [searchParams, setSearchParams] = useSearchParams();
  const versionParam = searchParams.get("version");
  const [selected, setSelected] = useState<RateCell | null>(null);
  const [detail, setDetail] = useState<RateSheetDetail | null>(null);
  const [state, setState] = useState("pending_review");
  const [error, setError] = useState("");
  const [commentCell, setCommentCell] = useState<RateCell | null>(null);
  const [activityKey, setActivityKey] = useState(0);

  const local = unionLocal(union);

  const loadDetail = useCallback(() => {
    setSelected(null);
    setError("");
    const qs = versionParam ? `?version=${encodeURIComponent(versionParam)}` : "";
    api
      .get<RateSheetDetail>(`/v1/unions/${local}/rate-sheets/${period}${qs}`)
      .then((r) => {
        setDetail(r);
        setState(r.approval_state ?? "pending_review");
      })
      .catch((e) => setError(String(e)));
  }, [local, period, versionParam]);

  useEffect(() => {
    loadDetail();
  }, [loadDetail]);

  // After any state-changing action, refresh both the detail (for approval pill)
  // and the activity timeline by bumping its key.
  const onActionChanged = (next: string) => {
    setState(next);
    setActivityKey((k) => k + 1);
    loadDetail();
  };
  const onCellActionSaved = () => setActivityKey((k) => k + 1);

  // Tier 3: switching the version dropdown re-loads via ?version=N.
  const onVersionSwitch = (v: number) => {
    if (v === detail?.versions?.[0]?.version) {
      // selecting the latest = no query param
      const next = new URLSearchParams(searchParams);
      next.delete("version");
      setSearchParams(next, { replace: true });
    } else {
      setSearchParams({ version: String(v) }, { replace: true });
    }
    setActivityKey((k) => k + 1);
  };

  // Called after a successful rework — bump to the new version and refresh.
  const onReworked = (toVersion: number) => {
    setSearchParams({ version: String(toVersion) }, { replace: true });
    setActivityKey((k) => k + 1);
    loadDetail();
  };

  const cells = detail?.cells ?? [];
  const artifacts = detail?.artifacts ?? [];
  const job = detail?.job_meta;
  const counts = detail?.counts ?? {};
  const gapsDetail: Array<[string, string, string, string]> =
    (detail as any)?.gaps_detail ?? [];
  // POC: there's no review-queue gate wired yet, so Approve is enabled as long
  // as we have cells. Tier 3 will tie this to unresolved comments/overrides.
  const reviewQueueEmpty = cells.length > 0;

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
            {/* Tier 3 — version pill + dropdown (only when there's more than
                one version in the chain). */}
            {detail && (detail.versions?.length ?? 0) > 0 && (
              <div className="flex items-center gap-1">
                <span
                  className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${
                    detail.version === detail.versions?.[0]?.version
                      ? "bg-slate-100 text-slate-700 ring-slate-200"
                      : "bg-amber-50 text-amber-800 ring-amber-200"
                  }`}
                  title={
                    detail.version === detail.versions?.[0]?.version
                      ? "Latest version"
                      : "Viewing a historical version"
                  }
                >
                  v{detail.version}
                  {detail.version === detail.versions?.[0]?.version
                    ? " · current"
                    : " · historical"}
                </span>
                {/* Show rework mode (merge vs ai) for any non-original version. */}
                {detail.parent_version &&
                  (() => {
                    const mode =
                      (detail.versions?.find((v) => v.version === detail.version)
                        ?.rework_context as { mode?: string } | null)?.mode ||
                      "merge";
                    return (
                      <span
                        className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${
                          mode === "ai"
                            ? "bg-indigo-100 text-indigo-800 ring-indigo-200"
                            : "bg-emerald-100 text-emerald-800 ring-emerald-200"
                        }`}
                        title={
                          mode === "ai"
                            ? "Reworked via AgentCore Runtime (AI re-extraction + overrides)"
                            : "Reworked via deterministic merge (parent + overrides)"
                        }
                      >
                        {mode === "ai" ? "✨ ai" : "merge"}
                      </span>
                    );
                  })()}
                {(detail.versions?.length ?? 0) > 1 && (
                  <select
                    value={String(detail.version ?? "")}
                    onChange={(e) => onVersionSwitch(Number(e.target.value))}
                    className="rounded-md border border-slate-200 bg-white px-2 py-0.5 text-xs text-slate-700 focus:border-brand focus:outline-none focus:ring-1 focus:ring-brand"
                    title="Switch version"
                  >
                    {detail.versions?.map((v) => (
                      <option key={v.version} value={v.version}>
                        v{v.version}
                        {v.parent_version
                          ? ` ← v${v.parent_version}`
                          : " (original)"}
                      </option>
                    ))}
                  </select>
                )}
              </div>
            )}
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

      {/* Gap warning — surfaces kernel-flagged cells the extractor knew it
          couldn't fill. The reviewer needs the per-cell reasons so they
          know what additional document to upload. Without this the sheet
          looks "complete with blanks" and the reviewer has no signal. */}
      {gapsDetail.length > 0 && (
        <div className="rounded-lg border border-amber-300 bg-amber-50 p-4 shadow-sm">
          <div className="flex items-start gap-3">
            <span className="mt-0.5 inline-flex h-6 w-6 flex-none items-center justify-center rounded-full bg-amber-100 text-amber-800 ring-1 ring-amber-300">
              !
            </span>
            <div className="flex-1">
              <p className="text-sm font-semibold text-amber-900">
                Extraction left {counts.gaps ?? 0} cell
                {(counts.gaps ?? 0) === 1 ? "" : "s"} blank — additional
                source documents needed
              </p>
              <p className="mt-1 text-xs text-amber-800">
                The extractor knew it couldn't fill these from the uploaded
                PDF{(detail as any)?.source_files?.uploads && ((detail as any).source_files.uploads as unknown[]).length > 1 ? "s" : ""}. Upload the supporting document(s) listed below
                into this period to fill the gaps automatically.
              </p>
              <ul className="mt-3 space-y-1.5">
                {gapsDetail.map(([zone, pkg, col, reason], i) => (
                  <li
                    key={i}
                    className="flex flex-wrap items-baseline gap-2 text-xs text-amber-900"
                  >
                    <span className="font-mono font-semibold">
                      {zone || "*"} · {pkg || "*"} · {col}
                    </span>
                    <span className="text-amber-700">— {reason}</span>
                  </li>
                ))}
              </ul>
            </div>
          </div>
        </div>
      )}

      {/* Tier 3 — rework action bar; visible only when the sheet is rejected
          on its latest version. Submits to POST /…/rework which creates v+1,
          applies overrides, regenerates the xlsx, and audit-logs the event. */}
      <ReworkBar
        union={union}
        period={period}
        approvalState={state}
        version={detail?.version}
        parentVersion={detail?.parent_version ?? null}
        onReworked={onReworked}
      />

      {/* ARTIFACT CARDS — prominent at top so the reviewer always knows what's
          downloadable. Clicking opens in a new tab; we don't auto-load any of
          them (a 50-page PDF inline would dominate the workflow). */}
      {(artifacts.length > 0 || job) && (
        <div className="rounded-lg border border-slate-200 bg-white shadow-sm">
          <div className="flex items-center justify-between border-b border-slate-100 px-5 py-3">
            <h3 className="text-sm font-semibold uppercase tracking-wide text-slate-500">
              Artifacts
            </h3>
            <span className="text-xs text-slate-500">
              {artifacts.filter((a) => a.url).length} of {artifacts.length} produced
            </span>
          </div>
          <div className="grid grid-cols-1 gap-3 p-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
            {artifacts.map((a) => (
              <ArtifactCard key={`${a.bucket}/${a.key || a.name}`} a={a} />
            ))}
            {job && (
              <Link
                to={`/admin/jobs/${encodeURIComponent(job.job_id)}`}
                className="block rounded-lg border border-slate-200 bg-white p-4 shadow-sm transition hover:border-brand hover:shadow"
              >
                <div className="flex items-start justify-between">
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span className="text-xs font-medium uppercase tracking-wide text-slate-500">
                        Pipeline
                      </span>
                      <span className="text-xs text-slate-300">·</span>
                      <span className="font-mono text-xs text-slate-500">
                        {job.status}
                      </span>
                    </div>
                    <div className="mt-1 font-medium text-slate-900">
                      Step Functions trace
                    </div>
                    <div className="mt-0.5 truncate font-mono text-xs text-slate-500">
                      job {job.job_id.slice(0, 16)}…
                    </div>
                  </div>
                  <span className="ml-2 mt-1 text-sm font-medium text-brand">
                    Admin ↗
                  </span>
                </div>
              </Link>
            )}
          </div>
        </div>
      )}

      {/* ACTION BAR */}
      <ApproveRejectBar
        union={union}
        period={period}
        approvalState={state}
        reviewQueueEmpty={reviewQueueEmpty}
        onChanged={onActionChanged}
      />

      {/* MAIN BODY — table + provenance. PDF is opened on demand via the
          Source PDF artifact card above; the data table gets the room. */}
      <div className="grid grid-cols-12 gap-3">
        <div className="col-span-9 overflow-hidden rounded-md border border-slate-200 bg-white shadow-sm">
          <div className="max-h-[640px] overflow-auto">
            <RateCellTable cells={cells} onSelect={setSelected} />
          </div>
        </div>
        <div className="col-span-3 overflow-hidden rounded-md border border-slate-200 bg-white shadow-sm">
          <div className="max-h-[640px] overflow-auto">
            <ProvenancePanel
              cell={selected}
              onComment={(c) => setCommentCell(c)}
            />
          </div>
        </div>
      </div>

      <ActivityTimeline local={local} period={period} refreshKey={activityKey} />

      <CellOverrideModal onSaved={onCellActionSaved} />
      {commentCell && (
        <CellCommentModal
          cellId={commentCell.cell_id}
          cellLabel={`${commentCell.package} · ${commentCell.column_name}`}
          onClose={() => setCommentCell(null)}
          onSaved={onCellActionSaved}
        />
      )}
    </div>
  );
}
