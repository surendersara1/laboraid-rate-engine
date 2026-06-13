import { RateSheetList } from "./RateSheetList";

export function Rejected(): JSX.Element {
  return (
    <RateSheetList
      state="rejected"
      title="Rejected"
      subtitle="Rate sheets sent back, with the rejection reason on each."
      badge="bg-rose-100 text-rose-800 ring-rose-200"
      emptyIcon="🚫"
      emptyMsg="No rejected rate sheets."
    />
  );
}
