import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { ApproveRejectBar } from "../components/ApproveRejectBar";
import { CellOverrideModal } from "../components/CellOverrideModal";
import { PdfViewer } from "../components/PdfViewer";
import { ProvenancePanel } from "../components/ProvenancePanel";
import { RateCellTable } from "../components/RateCellTable";
import { api } from "../lib/api";
import type { RateCell } from "../types/api";

interface RateSheetDetailResponse {
  approval_state?: string;
  cells: RateCell[];
  source_pdf_url?: string;
}

// `union` URL param is the display name (e.g. "Sprinkler 704"); extract the
// trailing local number so we can hit /v1/unions/{local}/rate-sheets/{period}.
function unionLocal(display: string): string {
  const m = display.match(/(\d{2,4})\s*$/);
  return m ? m[1] : display;
}

export function RateSheetReview(): JSX.Element {
  const { union = "", period = "" } = useParams();
  const [selected, setSelected] = useState<RateCell | null>(null);
  const [state, setState] = useState("pending_review");
  const [cells, setCells] = useState<RateCell[]>([]);
  const [pdfUrl, setPdfUrl] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    const local = unionLocal(union);
    api
      .get<RateSheetDetailResponse>(`/v1/unions/${local}/rate-sheets/${period}`)
      .then((r) => {
        setCells(r.cells ?? []);
        setPdfUrl(r.source_pdf_url ?? "");
        if (r.approval_state) setState(r.approval_state);
      })
      .catch((e) => setError(String(e)));
  }, [union, period]);

  const reviewQueueEmpty = cells.length === 0;

  return (
    <div className="flex h-full flex-col">
      <ApproveRejectBar
        union={union}
        period={period}
        approvalState={state}
        reviewQueueEmpty={reviewQueueEmpty}
        onChanged={setState}
      />
      {error && (
        <p className="bg-red-50 px-3 py-1 text-sm text-red-700">{error}</p>
      )}
      {/* 12-col grid: PDF takes 4, table takes 6, provenance takes 2 — so the
          cell table actually has room to breathe without long column names
          wrapping to 3 lines. */}
      <div className="grid flex-1 grid-cols-12 gap-3 p-3">
        <div className="col-span-4 min-h-[600px]">
          <PdfViewer url={pdfUrl} />
        </div>
        <div className="col-span-6 overflow-auto rounded-md border border-slate-200 bg-white shadow-sm">
          <RateCellTable cells={cells} onSelect={setSelected} />
        </div>
        <div className="col-span-2 rounded-md border border-slate-200 bg-white p-3 shadow-sm">
          <ProvenancePanel cell={selected} />
        </div>
      </div>
      <CellOverrideModal />
    </div>
  );
}
