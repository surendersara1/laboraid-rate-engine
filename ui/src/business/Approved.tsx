import { RateSheetList } from "./RateSheetList";

export function Approved(): JSX.Element {
  return (
    <RateSheetList
      state="approved"
      title="Approved"
      subtitle="Rate sheets signed off and ready to publish."
      badge="bg-emerald-100 text-emerald-800 ring-emerald-200"
      emptyIcon="✅"
      emptyMsg="No approved rate sheets yet."
    />
  );
}
