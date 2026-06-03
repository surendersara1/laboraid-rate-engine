import { useEffect, useState } from "react";
import { api } from "../lib/api";

export function Profiles(): JSX.Element {
  const [unions, setUnions] = useState<string[]>([]);
  useEffect(() => {
    api.get<{ unions: string[] }>("/v1/unions").then((r) => setUnions(r.unions));
  }, []);
  return (
    <div>
      <h2 className="mb-4 text-xl font-semibold">Profiles (read-only)</h2>
      <ul className="list-disc pl-6 text-sm">
        {unions.map((u) => (
          <li key={u}>{u}</li>
        ))}
      </ul>
    </div>
  );
}
