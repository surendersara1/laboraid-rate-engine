import { useState } from "react";
import { Document, Page, pdfjs } from "react-pdf";
import "react-pdf/dist/Page/AnnotationLayer.css";
import "react-pdf/dist/Page/TextLayer.css";

pdfjs.GlobalWorkerOptions.workerSrc = new URL(
  "pdfjs-dist/build/pdf.worker.min.mjs",
  import.meta.url,
).toString();

// Source PDF preview panel (Spec/09 §1.5 rate-sheet review, panel 1).
export function PdfViewer({ url }: { url: string }): JSX.Element {
  const [numPages, setNumPages] = useState(0);
  return (
    <div className="h-full overflow-auto border bg-white">
      <Document file={url} onLoadSuccess={(d) => setNumPages(d.numPages)}>
        {Array.from({ length: numPages }, (_, i) => (
          <Page key={i} pageNumber={i + 1} width={420} />
        ))}
      </Document>
    </div>
  );
}
