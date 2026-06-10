import { useState } from "react";
import { api } from "../lib/api";

interface FileStatus {
  name: string;
  state: "queued" | "uploading" | "done" | "error";
  detail?: string;
}

// Multi-file Admin upload. Selecting N PDFs uploads them in parallel via the
// existing /v1/uploads presigned-URL endpoint. Each PDF triggers its own SFN
// execution; the Publisher Lambda's merge mode unions them into a single
// rate_periods row when they share (union, period). See
// docs/design_multipdf_merge.md for the rationale.
export function Uploads(): JSX.Element {
  const [files, setFiles] = useState<FileStatus[]>([]);

  const uploadOne = async (file: File): Promise<FileStatus> => {
    try {
      const { url } = await api.post<{ url: string; key: string }>(
        "/v1/uploads",
        { filename: file.name },
      );
      const r = await fetch(url, { method: "PUT", body: file });
      if (!r.ok) throw new Error(`S3 PUT ${r.status}`);
      return { name: file.name, state: "done" };
    } catch (e) {
      return { name: file.name, state: "error", detail: String(e) };
    }
  };

  const handleChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const picked = Array.from(e.target.files ?? []);
    if (picked.length === 0) return;
    const initial: FileStatus[] = picked.map((f) => ({
      name: f.name,
      state: "uploading",
    }));
    setFiles(initial);
    // Parallel — S3 + API Gateway can comfortably take N concurrent uploads.
    const results = await Promise.all(picked.map((f) => uploadOne(f)));
    setFiles(results);
    e.target.value = "";
  };

  const stateColor: Record<FileStatus["state"], string> = {
    queued: "text-slate-500",
    uploading: "text-amber-700",
    done: "text-emerald-700",
    error: "text-rose-700",
  };

  return (
    <div>
      <h2 className="mb-4 text-xl font-semibold">Uploads</h2>
      <p className="mb-3 text-sm text-slate-600">
        Pick one or more PDFs. Multiple PDFs for the same (union, period) are
        merged into a single rate sheet — useful for unions that split rates
        across Apprentice / Journeymen / Residential files.
      </p>
      <input
        type="file"
        accept="application/pdf"
        multiple
        onChange={handleChange}
      />
      {files.length > 0 && (
        <ul className="mt-4 space-y-1 text-sm">
          {files.map((f) => (
            <li key={f.name} className="flex items-baseline gap-3">
              <span className={`font-medium ${stateColor[f.state]}`}>
                {f.state === "uploading"
                  ? "uploading…"
                  : f.state === "done"
                    ? "uploaded"
                    : f.state === "error"
                      ? "ERROR"
                      : "queued"}
              </span>
              <span className="text-slate-700">{f.name}</span>
              {f.detail && (
                <span className="text-xs text-rose-600">{f.detail}</span>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
