// Source PDF viewer for the rate-sheet review page. The react-pdf embed's
// pdfjs worker bundle wasn't resolving against CloudFront and the iframe
// gave the user a misleading "Failed to load PDF file" message. For the
// POC we use the browser's native PDF viewer in an iframe and provide
// "Open in new tab" so there's always a working way to see the source.
export function PdfViewer({ url }: { url: string }): JSX.Element {
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
          Source PDF
        </span>
        <a
          href={url}
          target="_blank"
          rel="noreferrer"
          className="text-xs font-medium text-brand hover:text-brand-dark"
        >
          Open in new tab ↗
        </a>
      </div>
      <iframe
        src={url}
        title="Source rate notice"
        className="h-full w-full flex-1 rounded-b-md border-0"
      />
    </div>
  );
}
