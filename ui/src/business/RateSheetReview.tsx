import { useState } from "react";
import { useParams } from "react-router-dom";
import { ApproveRejectBar } from "../components/ApproveRejectBar";
import { CellOverrideModal } from "../components/CellOverrideModal";
import { PdfViewer } from "../components/PdfViewer";
import { ProvenancePanel } from "../components/ProvenancePanel";
import { RateCellTable } from "../components/RateCellTable";
import type { RateCell } from "../types/api";

export function RateSheetReview(): JSX.Element {
  const { union = "", period = "" } = useParams();
  const [selected, setSelected] = useState<RateCell | null>(null);
  const [state, setState] = useState("pending_review");
  const cells: RateCell[] = [];
  const reviewQueueEmpty = true;

  return (
    <div className="flex h-full flex-col">
      <ApproveRejectBar
        union={union}
        period={period}
        approvalState={state}
        reviewQueueEmpty={reviewQueueEmpty}
        onChanged={setState}
      />
      <div className="grid flex-1 grid-cols-3 gap-2 p-2">
        <PdfViewer url="" />
        <div className="overflow-auto border bg-white">
          <RateCellTable cells={cells} onSelect={setSelected} />
        </div>
        <div className="border bg-white">
          <ProvenancePanel cell={selected} />
        </div>
      </div>
      <CellOverrideModal />
    </div>
  );
}
