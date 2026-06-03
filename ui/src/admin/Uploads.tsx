import { useState } from "react";
import { api } from "../lib/api";

export function Uploads(): JSX.Element {
  const [status, setStatus] = useState("");

  const upload = async (file: File) => {
    setStatus("requesting URL…");
    const { url } = await api.post<{ url: string; key: string }>("/v1/uploads", {
      filename: file.name,
    });
    setStatus("uploading…");
    await fetch(url, { method: "PUT", body: file });
    setStatus(`uploaded ${file.name}`);
  };

  return (
    <div>
      <h2 className="mb-4 text-xl font-semibold">Uploads</h2>
      <input
        type="file"
        accept="application/pdf"
        onChange={(e) => {
          const f = e.target.files?.[0];
          if (f) void upload(f);
        }}
      />
      <p className="mt-3 text-sm text-slate-600">{status}</p>
    </div>
  );
}
