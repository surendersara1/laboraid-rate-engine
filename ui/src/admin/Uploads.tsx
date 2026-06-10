import { useState } from "react";
import { api } from "../lib/api";

interface FileStatus {
  name: string;
  state: "queued" | "hashing" | "uploading" | "done" | "duplicate" | "error";
  detail?: string;
}

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

// YYYY.MM.DD.<local> ...     — Rate Notice / Wage Sheet (single anchor date)
const ANCHOR_FILENAME_RE = /^(\d{4})\.(\d{2})\.(\d{2})\.\d{3}\s+(.+?)\.pdf$/i;

// Pick the batch's anchor period from filenames. The browser's job is to
// detect the one (or most recent) clean-date filename in the batch and
// declare its date as the target rate period for the whole batch — that
// lets CBAs and Apprentice Scales (which have range or no dates) inherit
// the right rate period downstream. Returns null when no anchor exists,
// in which case each file falls back to using its own filename date.
function inferBatchPeriod(filenames: string[]): string | null {
  const candidates: string[] = [];
  for (const name of filenames) {
    const m = ANCHOR_FILENAME_RE.exec(name);
    if (!m) continue;
    const doc = m[4].toLowerCase();
    // Apprentice Scale PDFs can carry a clean date too, but they should
    // INHERIT the Rate Notice's period (a scale revision often takes
    // effect on a different date than the wage notice itself). So we
    // only count Rate Notice / Wage Sheet / Rate Sheet filenames as
    // legitimate anchors.
    if (
      doc.includes("rate notice") ||
      doc.includes("rate sheet") ||
      doc.includes("wage sheet")
    ) {
      candidates.push(`${m[1]}-${m[2]}-${m[3]}`);
    }
  }
  if (candidates.length === 0) return null;
  // Most recent date wins (if a batch somehow has multiple Rate Notices,
  // the later one is what the reviewer is most likely actioning today).
  candidates.sort();
  return candidates[candidates.length - 1];
}

// Compute SHA-256 hex via the browser's SubtleCrypto. Used for dedup so the
// server can skip the pipeline + Bedrock cost on an identical re-upload.
async function sha256Hex(file: File): Promise<string> {
  const buf = await file.arrayBuffer();
  const digest = await crypto.subtle.digest("SHA-256", buf);
  return Array.from(new Uint8Array(digest))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

// Multi-file Admin upload. Selecting N PDFs uploads them in parallel via the
// existing /v1/uploads presigned-URL endpoint. The browser mints one batch_id
// per multi-select click so the pipeline can group related uploads
// downstream. Each file's content is hashed (SHA-256) so the server can
// dedup identical re-uploads (skip the Bedrock cost).
// See docs/Design/design_upload_grouping_and_idempotency.md for the rationale.
export function Uploads(): JSX.Element {
  const [files, setFiles] = useState<FileStatus[]>([]);
  const [lastBatchId, setLastBatchId] = useState<string | null>(null);

  const uploadOne = async (
    file: File,
    batchId: string,
    batchPeriod: string | null,
    updateStatus: (s: FileStatus) => void,
  ): Promise<FileStatus> => {
    try {
      updateStatus({ name: file.name, state: "hashing" });
      const contentHash = await sha256Hex(file);

      updateStatus({ name: file.name, state: "uploading" });
      const presign = await api.post<PresignResponse>("/v1/uploads", {
        filename: file.name,
        batch_id: batchId,
        batch_period: batchPeriod ?? undefined,
        content_hash: contentHash,
      });
      if (presign.status === "duplicate") {
        return {
          name: file.name,
          state: "duplicate",
          detail: presign.existing_period_id
            ? `already processed (period ${presign.existing_period_id})`
            : "already processed",
        };
      }
      if (!presign.url) throw new Error("no presigned URL");
      const r = await fetch(presign.url, { method: "PUT", body: file });
      if (!r.ok) throw new Error(`S3 PUT ${r.status}`);
      return { name: file.name, state: "done" };
    } catch (e) {
      return { name: file.name, state: "error", detail: String(e) };
    }
  };

  const [lastBatchPeriod, setLastBatchPeriod] = useState<string | null>(null);

  const handleChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const picked = Array.from(e.target.files ?? []);
    if (picked.length === 0) return;
    const batchId = crypto.randomUUID();
    const batchPeriod = inferBatchPeriod(picked.map((f) => f.name));
    setLastBatchId(batchId);
    setLastBatchPeriod(batchPeriod);
    const initial: FileStatus[] = picked.map((f) => ({
      name: f.name,
      state: "hashing",
    }));
    setFiles(initial);
    const updateOne = (status: FileStatus) =>
      setFiles((prev) => prev.map((f) => (f.name === status.name ? status : f)));
    const results = await Promise.all(
      picked.map((f) => uploadOne(f, batchId, batchPeriod, updateOne)),
    );
    setFiles(results);
    e.target.value = "";
  };

  const STATE_LABEL: Record<FileStatus["state"], string> = {
    queued: "queued",
    hashing: "hashing…",
    uploading: "uploading…",
    done: "uploaded",
    duplicate: "duplicate (skipped)",
    error: "ERROR",
  };
  const STATE_COLOR: Record<FileStatus["state"], string> = {
    queued: "text-slate-500",
    hashing: "text-sky-700",
    uploading: "text-amber-700",
    done: "text-emerald-700",
    duplicate: "text-slate-500",
    error: "text-rose-700",
  };

  return (
    <div>
      <h2 className="mb-4 text-xl font-semibold">Uploads</h2>
      <p className="mb-3 text-sm text-slate-600">
        Pick one or more PDFs. Multiple PDFs for the same (union, period) are
        merged into a single rate sheet. Re-uploading the exact same file is
        detected and skipped (no Bedrock cost).
      </p>
      <input
        type="file"
        accept="application/pdf"
        multiple
        onChange={handleChange}
      />
      {lastBatchId && (
        <p className="mt-3 text-xs text-slate-500">
          batch <span className="font-mono">{lastBatchId.slice(0, 8)}…</span>
          {lastBatchPeriod ? (
            <>
              {" "}
              <span className="text-slate-400">·</span> anchor period{" "}
              <span className="font-mono text-slate-700">{lastBatchPeriod}</span>
            </>
          ) : (
            <>
              {" "}
              <span className="text-slate-400">·</span>{" "}
              <span className="text-amber-600">no anchor Rate Notice detected</span>
            </>
          )}
        </p>
      )}
      {files.length > 0 && (
        <ul className="mt-4 space-y-1 text-sm">
          {files.map((f) => (
            <li key={f.name} className="flex items-baseline gap-3">
              <span className={`font-medium ${STATE_COLOR[f.state]}`}>
                {STATE_LABEL[f.state]}
              </span>
              <span className="text-slate-700">{f.name}</span>
              {f.detail && (
                <span className="text-xs text-slate-500">{f.detail}</span>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
