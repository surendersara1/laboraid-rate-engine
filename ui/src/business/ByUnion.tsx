import { useParams } from "react-router-dom";

export function ByUnion(): JSX.Element {
  const { union } = useParams();
  return (
    <div>
      <h2 className="mb-4 text-xl font-semibold">By Union · {union ?? "all"}</h2>
      <p className="text-sm text-slate-600">
        All rate sheets for one union with status badges (POC stub).
      </p>
    </div>
  );
}
