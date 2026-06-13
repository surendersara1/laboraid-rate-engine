import { RateSheetList } from "./RateSheetList";

export function ReviewQueue(): JSX.Element {
  return (
    <RateSheetList
      state="pending_review"
      title="Review Queue"
      subtitle="All rate sheets awaiting business sign-off."
      badge="bg-amber-100 text-amber-800 ring-amber-200"
      emptyIcon="✅"
      emptyMsg="The queue is clear — nothing awaiting review."
    />
  );
}
