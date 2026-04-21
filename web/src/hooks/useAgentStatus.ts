import { useQuery } from "@tanstack/react-query";
import { getAgentStatus } from "../api/agents";
import type { AgentStatusResponse } from "../types/api";

export function useAgentStatus(agentId: string) {
  return useQuery<AgentStatusResponse, Error>({
    queryKey: ["agentStatus", agentId],
    queryFn: () => getAgentStatus(agentId),
    refetchInterval: 10_000,
  });
}
