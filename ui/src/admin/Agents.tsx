import { useCallback, useEffect, useState } from "react";
import { AgentToggle } from "../components/AgentToggle";
import { api } from "../lib/api";
import { usePolling } from "../lib/usePolling";
import type { AgentConfig } from "../types/api";

export function Agents(): JSX.Element {
  const [agents, setAgents] = useState<AgentConfig[]>([]);

  const load = useCallback(() => {
    api.get<{ agents: AgentConfig[] }>("/v1/agents").then((r) => setAgents(r.agents));
  }, []);
  useEffect(load, [load]);
  usePolling(load, agents.length > 0);

  return (
    <div>
      <h2 className="mb-4 text-xl font-semibold">Agents</h2>
      <div className="space-y-2">
        {agents.map((a) => (
          <div
            key={a.agent_name}
            className="flex items-center justify-between rounded border bg-white p-3"
          >
            <span>{a.agent_name}</span>
            <AgentToggle
              name={a.agent_name}
              enabled={a.enabled}
              onChange={(enabled) =>
                setAgents((prev) =>
                  prev.map((x) =>
                    x.agent_name === a.agent_name ? { ...x, enabled } : x,
                  ),
                )
              }
            />
          </div>
        ))}
        {agents.length === 0 && (
          <p className="text-slate-500">No agents registered.</p>
        )}
      </div>
    </div>
  );
}
