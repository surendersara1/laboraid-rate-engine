import { useCallback, useEffect, useState } from "react";
import { api } from "../lib/api";
import { usePolling } from "../lib/usePolling";
import type { Job } from "../types/api";

export function Jobs(): JSX.Element {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [error, setError] = useState("");

  const load = useCallback(() => {
    api
      .get<{ jobs: Job[] }>("/v1/jobs")
      .then((r) => setJobs(r.jobs))
      .catch((e) => setError(String(e)));
  }, []);

  useEffect(load, [load]);
  const anyInProgress = jobs.some((j) => j.status === "in_progress");
  usePolling(load, anyInProgress);

  if (error) return <p className="text-red-600">{error}</p>;
  if (jobs.length === 0) return <p className="text-slate-500">No jobs yet.</p>;
  return (
    <div>
      <h2 className="mb-4 text-xl font-semibold">Jobs</h2>
      <table className="w-full text-sm">
        <thead className="bg-slate-100">
          <tr>
            <th className="px-2 py-1 text-left">Job</th>
            <th className="px-2 py-1 text-left">Status</th>
          </tr>
        </thead>
        <tbody>
          {jobs.map((j) => (
            <tr key={j.job_id} className="border-b">
              <td className="px-2 py-1">{j.job_id}</td>
              <td className="px-2 py-1">{j.status}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
