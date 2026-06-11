import { useMemo, useState } from "react";
import { api } from "../lib/api";

interface PresignResponse {
  status: "ready" | "duplicate";
  url?: string;
  key?: string;
  batch_id?: string | null;
  batch_period?: string | null;
  // populated when status === "duplicate":
  content_hash?: string;
  existing_period_id?: string;
  existing_s3_key?: string;
}

type StagedRole =
  | "rate_notice"
  | "rate_sheet"
  | "cba"
  | "apprentice_scale"
  | "unknown";

interface StagedFile {
  file: File;
  id: string; // local UI id (not the batch_id)
  role: StagedRole;
  anchorDate: string | null; // YYYY-MM-DD if a single anchor date can be parsed
  status: "staged" | "hashing" | "uploading" | "done" | "duplicate" | "error";
  detail?: string;
  s3Key?: string; // the inputs-bucket key once uploaded (or the existing one if dup)
}

// YYYY.MM.DD.<local> ...     — Rate Notice / Wage Sheet (single anchor date)
const ANCHOR_FILENAME_RE = /^(\d{4})\.(\d{2})\.(\d{2})\.\d{3}\s+(.+?)\.pdf$/i;
const RANGE_FILENAME_RE =
  /^(\d{4})\.(\d{2})\.(\d{2})[-–](\d{4})\.(\d{2})\.(\d{2})\.\d{3}\s+(.+?)\.pdf$/i;

function classifyFilename(name: string): { role: StagedRole; anchor: string | null } {
  const m = ANCHOR_FILENAME_RE.exec(name);
  if (m) {
    const doc = m[4].toLowerCase();
    const anchor = `${m[1]}-${m[2]}-${m[3]}`;
    if (doc.includes("apprentice") && doc.includes("scale")) return { role: "apprentice_scale", anchor };
    if (doc.includes("apprentice") && doc.includes("wage")) return { role: "apprentice_scale", anchor };
    if (doc.includes("trainee")) return { role: "apprentice_scale", anchor };
    if (doc.includes("rate notice")) return { role: "rate_notice", anchor };
    if (doc.includes("rate sheet") || doc.includes("wage sheet"))
      return { role: "rate_sheet", anchor };
    if (doc.includes("cba") || doc.includes("agreement")) return { role: "cba", anchor };
    return { role: "unknown", anchor };
  }
  const r = RANGE_FILENAME_RE.exec(name);
  if (r) {
    const doc = r[7].toLowerCase();
    if (doc.includes("cba") || doc.includes("agreement")) return { role: "cba", anchor: null };
    return { role: "unknown", anchor: null };
  }
  return { role: "unknown", anchor: null };
}

// Anchor period = the most recent YYYY.MM.DD date among Rate Notice / Wage
// Rate Sheet / Wage Sheet files. CBAs (range dates) and Apprentice Scales
// do NOT anchor — they inherit from the Rate Notice.
function inferBatchPeriod(staged: StagedFile[]): string | null {
  const candidates: string[] = [];
  for (const s of staged) {
    if (s.anchorDate && (s.role === "rate_notice" || s.role === "rate_sheet")) {
      candidates.push(s.anchorDate);
    }
  }
  if (candidates.length === 0) return null;
  candidates.sort();
  return candidates[candidates.length - 1];
}

async function sha256Hex(file: File): Promise<string> {
  const buf = await file.arrayBuffer();
  const digest = await crypto.subtle.digest("SHA-256", buf);
  return Array.from(new Uint8Array(digest))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

const ROLE_LABEL: Record<StagedRole, string> = {
  rate_notice: "Rate Notice",
  rate_sheet: "Wage Rate Sheet",
  cba: "CBA",
  apprentice_scale: "Apprentice Scale",
  unknown: "Unknown",
};
const ROLE_COLOR: Record<StagedRole, string> = {
  rate_notice: "bg-emerald-100 text-emerald-800 ring-emerald-200",
  rate_sheet: "bg-sky-100 text-sky-800 ring-sky-200",
  cba: "bg-indigo-100 text-indigo-800 ring-indigo-200",
  apprentice_scale: "bg-violet-100 text-violet-800 ring-violet-200",
  unknown: "bg-slate-100 text-slate-700 ring-slate-200",
};
const STATUS_LABEL: Record<StagedFile["status"], string> = {
  staged: "ready to process",
  hashing: "hashing…",
  uploading: "uploading…",
  done: "uploaded",
  duplicate: "duplicate (skipped)",
  error: "ERROR",
};
const STATUS_COLOR: Record<StagedFile["status"], string> = {
  staged: "text-slate-500",
  hashing: "text-sky-700",
  uploading: "text-amber-700",
  done: "text-emerald-700",
  duplicate: "text-slate-500",
  error: "text-rose-700",
};

// Staged → Process: nothing reaches AWS until "Process this batch" is
// clicked. The reviewer can add files incrementally ("add more"), remove
// mistakes (×), and see the inferred anchor period + per-file role
// classification before any S3 PUT or SFN spend happens.
export function Uploads(): JSX.Element {
  const [staged, setStaged] = useState<StagedFile[]>([]);
  const [activeBatch, setActiveBatch] = useState<{
    batch_id: string;
    batch_period: string | null;
    finished: boolean;
  } | null>(null);

  const anchorPeriod = useMemo(() => inferBatchPeriod(staged), [staged]);
  const rateNoticeCount = staged.filter((s) => s.role === "rate_notice").length;
  const rateSheetCount = staged.filter((s) => s.role === "rate_sheet").length;
  const processing =
    staged.length > 0 && staged.some((s) => s.status === "hashing" || s.status === "uploading");
  const allDone =
    staged.length > 0 &&
    staged.every((s) => s.status === "done" || s.status === "duplicate" || s.status === "error");

  const addFiles = (picked: File[]) => {
    if (picked.length === 0) return;
    setStaged((prev) => {
      const existingNames = new Set(prev.map((s) => s.file.name));
      const fresh = picked
        .filter((f) => !existingNames.has(f.name))
        .map((f) => {
          const c = classifyFilename(f.name);
          return {
            file: f,
            id: `${f.name}__${f.size}`,
            role: c.role,
            anchorDate: c.anchor,
            status: "staged" as const,
          };
        });
      return [...prev, ...fresh];
    });
  };

  const onPick = (e: React.ChangeEvent<HTMLInputElement>) => {
    addFiles(Array.from(e.target.files ?? []));
    e.target.value = "";
  };

  const removeFile = (id: string) =>
    setStaged((prev) => prev.filter((s) => s.id !== id));

  const clearAll = () => {
    setStaged([]);
    setActiveBatch(null);
  };

  const uploadOne = async (
    s: StagedFile,
    batchId: string,
    batchPeriod: string | null,
    updateOne: (id: string, patch: Partial<StagedFile>) => void,
    force = false,
  ): Promise<{ s3_key: string; filename: string } | null> => {
    try {
      updateOne(s.id, { status: "hashing" });
      const contentHash = await sha256Hex(s.file);
      updateOne(s.id, { status: "uploading" });
      const presign = await api.post<PresignResponse>("/v1/uploads", {
        filename: s.file.name,
        batch_id: batchId,
        batch_period: batchPeriod ?? undefined,
        content_hash: contentHash,
        ...(force ? { force: true } : {}),
      });
      if (presign.status === "duplicate") {
        updateOne(s.id, {
          status: "duplicate",
          s3Key: presign.existing_s3_key,
          detail: presign.existing_period_id
            ? `already processed (period ${presign.existing_period_id.slice(0, 8)}…)`
            : "already processed",
        });
        return presign.existing_s3_key
          ? { s3_key: presign.existing_s3_key, filename: s.file.name }
          : null;
      }
      if (!presign.url) throw new Error("no presigned URL returned");
      const r = await fetch(presign.url, { method: "PUT", body: s.file });
      if (!r.ok) throw new Error(`S3 PUT ${r.status}`);
      updateOne(s.id, { status: "done", s3Key: presign.key });
      return presign.key ? { s3_key: presign.key, filename: s.file.name } : null;
    } catch (e) {
      updateOne(s.id, { status: "error", detail: String(e) });
      return null;
    }
  };

  // Force re-process a single file the dedup gate skipped: re-runs the
  // presign with force=true (server deletes the prior content-hash row),
  // PUTs the PDF, and the pipeline extracts it fresh. The new cells MERGE
  // into the existing period (filling NULLs / appending); audit trail keeps
  // both batch ids.
  const forceOne = async (id: string) => {
    const target = staged.find((s) => s.id === id);
    if (!target || !activeBatch) return;
    const updateOne = (sid: string, patch: Partial<StagedFile>) =>
      setStaged((prev) => prev.map((s) => (s.id === sid ? { ...s, ...patch } : s)));
    await uploadOne(target, activeBatch.batch_id, activeBatch.batch_period, updateOne, true);
  };

  const processNow = async () => {
    if (staged.length === 0) return;
    const batchId = crypto.randomUUID();
    const batchPeriod = anchorPeriod;
    setActiveBatch({ batch_id: batchId, batch_period: batchPeriod, finished: false });
    const updateOne = (id: string, patch: Partial<StagedFile>) =>
      setStaged((prev) => prev.map((s) => (s.id === id ? { ...s, ...patch } : s)));
    // 1. Upload every staged file to S3 (concurrent PUTs are fine — they don't
    //    touch Aurora; only the staging bucket). Each returns its manifest entry.
    const entries = await Promise.all(
      staged.map((s) => uploadOne(s, batchId, batchPeriod, updateOne)),
    );
    // 2. Kick ONE sequential pipeline run with the full manifest. The planner
    //    sorts (CBA first, then by date) and the SFN applies docs one-at-a-time
    //    — no parallel race, no duplicate cells.
    const files = entries.filter((e): e is { s3_key: string; filename: string } => e != null);
    if (files.length > 0) {
      try {
        await api.post("/v1/batches/process", {
          batch_id: batchId,
          batch_period: batchPeriod ?? undefined,
          files,
        });
      } catch (e) {
        console.error("batch process start failed:", e);
      }
    }
    setActiveBatch((b) => (b ? { ...b, finished: true } : b));
  };

  const startNewBatch = () => clearAll();

  const noAnchorWarn = staged.length > 0 && !anchorPeriod;
  const multiAnchorWarn = rateNoticeCount + rateSheetCount > 1 && anchorPeriod;

  return (
    <div className="space-y-4">
      <div>
        <h2 className="text-xl font-semibold text-slate-900">Uploads</h2>
        <p className="mt-1 text-sm text-slate-600">
          Stage all the PDFs that belong to one rate period (Rate Notice + Wage Rate Sheet
          + CBA, etc.), then click <b>Process this batch</b>. Nothing reaches AWS — no
          presign, no Bedrock spend, no rate_period row in Aurora — until you click Process.
        </p>
      </div>

      {/* STAGING CARD */}
      <div className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm">
        <div className="flex flex-wrap items-center gap-3">
          <label className="cursor-pointer rounded-md bg-brand px-3 py-1.5 text-sm font-medium text-white shadow-sm hover:bg-brand-dark">
            {staged.length === 0 ? "+ Add PDFs" : "+ Add more"}
            <input
              type="file"
              accept="application/pdf"
              multiple
              onChange={onPick}
              className="hidden"
              disabled={processing}
            />
          </label>
          {staged.length > 0 && !processing && !allDone && (
            <button
              onClick={clearAll}
              className="text-sm text-slate-500 hover:text-slate-700"
            >
              Clear staged
            </button>
          )}
          {allDone && (
            <button
              onClick={startNewBatch}
              className="text-sm text-slate-600 hover:text-slate-900 underline"
            >
              Start a new batch
            </button>
          )}
        </div>

        {staged.length === 0 ? (
          <p className="mt-4 text-xs text-slate-500">
            No files staged yet. Click <b>Add PDFs</b> to begin — you can add more
            in repeated picks; nothing is uploaded until you press Process.
          </p>
        ) : (
          <>
            <div className="mt-4">
              <p className="text-xs uppercase tracking-wide text-slate-500">
                Staged for this run ({staged.length} file{staged.length === 1 ? "" : "s"})
              </p>
              <ul className="mt-2 space-y-1.5">
                {staged.map((s) => (
                  <li
                    key={s.id}
                    className="flex items-baseline gap-3 rounded-md border border-slate-100 bg-slate-50 px-3 py-2"
                  >
                    <span
                      className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${ROLE_COLOR[s.role]}`}
                    >
                      {ROLE_LABEL[s.role]}
                    </span>
                    {s.anchorDate && (
                      <span className="font-mono text-xs text-slate-500">
                        {s.anchorDate}
                      </span>
                    )}
                    <span className="flex-1 truncate text-sm text-slate-700">
                      {s.file.name}
                    </span>
                    <span
                      className={`text-xs font-medium ${STATUS_COLOR[s.status]}`}
                    >
                      {STATUS_LABEL[s.status]}
                    </span>
                    {s.detail && (
                      <span className="text-xs text-slate-500">{s.detail}</span>
                    )}
                    {s.status === "duplicate" && (
                      <button
                        onClick={() => forceOne(s.id)}
                        className="rounded-md border border-amber-300 bg-amber-50 px-2 py-0.5 text-xs font-medium text-amber-800 hover:bg-amber-100"
                        title="Bypass the duplicate check and re-extract this PDF with the current pipeline. New values merge into the existing period; the audit trail records the re-run."
                      >
                        Force re-process
                      </button>
                    )}
                    {s.status === "staged" && (
                      <button
                        onClick={() => removeFile(s.id)}
                        className="text-slate-400 hover:text-rose-600"
                        title="remove"
                      >
                        ×
                      </button>
                    )}
                  </li>
                ))}
              </ul>
            </div>

            {/* Inferred target period */}
            <div className="mt-4 rounded-md border border-slate-200 bg-white px-4 py-3">
              <p className="text-xs uppercase tracking-wide text-slate-500">
                Target rate period
              </p>
              {anchorPeriod ? (
                <p className="mt-1 text-sm font-semibold text-slate-900">
                  <span className="font-mono">{anchorPeriod}</span>
                  <span className="ml-2 text-xs font-normal text-slate-500">
                    inferred from the Rate Notice / Wage Rate Sheet in this batch
                  </span>
                </p>
              ) : (
                <p className="mt-1 text-sm font-semibold text-amber-700">
                  no anchor detected — please add a Rate Notice or Wage Rate Sheet
                  with a YYYY.MM.DD filename, or use a single-CBA batch (period will
                  fall back to the CBA's start date).
                </p>
              )}
            </div>

            {noAnchorWarn && (
              <p className="mt-2 rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-900">
                ⚠ This batch has no Rate Notice / Wage Rate Sheet to anchor it. The
                CBA will end up at its filename start date — usually not what you
                want. Add a Rate Notice to anchor the period.
              </p>
            )}
            {multiAnchorWarn && (
              <p className="mt-2 rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-900">
                ⚠ Multiple Rate Notice / Wage Rate Sheet files detected. Using the
                most recent ({anchorPeriod}) as the anchor. If you meant separate
                periods, upload them in separate batches.
              </p>
            )}

            <div className="mt-5 flex flex-wrap items-center gap-3">
              <button
                onClick={processNow}
                disabled={processing || allDone}
                className="rounded-md bg-emerald-600 px-4 py-2 text-sm font-semibold text-white shadow-sm hover:bg-emerald-700 disabled:cursor-not-allowed disabled:bg-slate-300"
              >
                {processing
                  ? "Processing…"
                  : allDone
                    ? "Processed"
                    : "▶ Process this batch"}
              </button>
              {activeBatch && (
                <p className="text-xs text-slate-500">
                  batch{" "}
                  <span className="font-mono text-slate-700">
                    {activeBatch.batch_id.slice(0, 8)}…
                  </span>
                  {activeBatch.batch_period && (
                    <>
                      {" "}
                      · period{" "}
                      <span className="font-mono text-slate-700">
                        {activeBatch.batch_period}
                      </span>
                    </>
                  )}
                </p>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
