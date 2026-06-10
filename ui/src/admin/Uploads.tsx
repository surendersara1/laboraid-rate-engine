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
  // populated when status === "duplicate":
  content_hash?: string;
  existing_period_id?: string;
  existing_s3_key?: string;
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
// See docs/design_upload_grouping_and_idempotency.md for the rationale.
export function Uploads(): JSX.Element {
  const [files, setFiles] = useState<FileStatus[]>([]);
  const [lastBatchId, setLastBatchId] = useState<string | null>(null);

  const uploadOne = async (
    file: File,
    batchId: string,
    updateStatus: (s: FileStatus) => void,
  ): Promise<FileStatus> => {
    try {
      updateStatus({ name: file.name, state: "hashing" });
      const contentHash = await sha256Hex(file);

      updateStatus({ name: file.name, state: "uploading" });
      const presign = await api.post<PresignResponse>("/v1/uploads", {
        filename: file.name,
        batch_id: batchId,
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

  const handleChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const picked = Array.from(e.target.files ?? []);
    if (picked.length === 0) return;
    const batchId = crypto.randomUUID();
    setLastBatchId(batchId);
    const initial: FileStatus[] = picked.map((f) => ({
      name: f.name,
      state: "hashing",
    }));
    setFiles(initial);
    const updateOne = (status: FileStatus) =>
      setFiles((prev) => prev.map((f) => (f.name === status.name ? status : f)));
    const results = await Promise.all(
      picked.map((f) => uploadOne(f, batchId, updateOne)),
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
