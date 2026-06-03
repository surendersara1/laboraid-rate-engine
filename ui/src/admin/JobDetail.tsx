import { useParams } from "react-router-dom";

export function JobDetail(): JSX.Element {
  const { id } = useParams();
  return (
    <div>
      <h2 className="mb-4 text-xl font-semibold">Job {id}</h2>
      <p className="text-sm text-slate-600">
        Per-stage timeline, CloudWatch deep-links, retry / abort (POC stub).
      </p>
    </div>
  );
}
