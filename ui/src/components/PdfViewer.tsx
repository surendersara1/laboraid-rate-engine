import { useEffect, useState } from "react";
import { Document, Page, pdfjs } from "react-pdf";
import "react-pdf/dist/Page/AnnotationLayer.css";
import "react-pdf/dist/Page/TextLayer.css";

// Pin to the same pdfjs-dist version vite installed (see ui/pnpm-lock.yaml).
// We tried `?url` to bundle the worker, but Rollup couldn't resolve it under
// pnpm's hoisting; the CDN URL works without any vite config changes and
// unpkg sets permissive CORS for the worker fetch.
const PDFJS_VERSION = "4.4.168";
pdfjs.GlobalWorkerOptions.workerSrc =
  `https://unpkg.com/pdfjs-dist@${PDFJS_VERSION}/build/pdf.worker.min.mjs`;

// Source PDF viewer (Tier 1.2). Renders the PDF inline using PDF.js. No
// download dialog, no native browser PDF plugin — just a scrollable, zoomable
// canvas the business reviewer can read alongside the extracted cell table.
export function PdfViewer({ url }: { url: string }): JSX.Element {
  const [numPages, setNumPages] = useState(0);
  const [error, setError] = useState("");
  const [zoom, setZoom] = useState(1.0);

  useEffect(() => {
    setNumPages(0);
    setError("");
    setZoom(1.0);
  }, [url]);

  if (!url) {
    return (
      <div className="flex h-full items-center justify-center rounded-md border border-slate-200 bg-slate-50 p-6 text-center text-sm text-slate-500">
        Source PDF not available for this period.
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col rounded-md border border-slate-200 bg-white">
      <div className="flex items-center justify-between border-b border-slate-100 px-3 py-2">
        <span className="text-xs font-medium uppercase tracking-wide text-slate-500">
          Source PDF{numPages ? ` · ${numPages} page${numPages === 1 ? "" : "s"}` : ""}
        </span>
        <div className="flex items-center gap-2 text-xs">
          <button
            type="button"
            onClick={() => setZoom((z) => Math.max(0.5, z - 0.1))}
            className="rounded px-2 py-0.5 text-slate-600 hover:bg-slate-100"
            title="Zoom out"
          >
            −
          </button>
          <span className="w-10 text-center font-mono text-slate-600">
            {Math.round(zoom * 100)}%
          </span>
          <button
            type="button"
            onClick={() => setZoom((z) => Math.min(2.0, z + 0.1))}
            className="rounded px-2 py-0.5 text-slate-600 hover:bg-slate-100"
            title="Zoom in"
          >
            +
          </button>
          <a
            href={url}
            target="_blank"
            rel="noreferrer"
            className="ml-2 font-medium text-brand hover:text-brand-dark"
          >
            Open ↗
          </a>
        </div>
      </div>
      <div className="flex-1 overflow-auto bg-slate-50 p-2">
        {error ? (
          <p className="p-4 text-sm text-rose-600">{error}</p>
        ) : (
          <Document
            file={url}
            onLoadSuccess={(d) => setNumPages(d.numPages)}
            onLoadError={(e) => setError(`PDF failed to load: ${e.message}`)}
            loading={<p className="p-4 text-sm text-slate-500">Loading PDF…</p>}
          >
            {Array.from({ length: numPages }, (_, i) => (
              <div key={i} className="mb-2 flex justify-center">
                <Page
                  pageNumber={i + 1}
                  width={420 * zoom}
                  renderAnnotationLayer={false}
                  renderTextLayer={false}
                />
              </div>
            ))}
          </Document>
        )}
      </div>
    </div>
  );
}
