import { useQuery } from "@tanstack/react-query";
import { getAgent, getAgents } from "../api/agents";
import type { AgentResponse } from "../types/api";

export function useAgents() {
  return useQuery<AgentResponse[], Error>({
    queryKey: ["agents"],
    queryFn: getAgents,
  });
}

export function useAgent(id: string) {
  return useQuery<AgentResponse, Error>({
    queryKey: ["agents", id],
    queryFn: () => getAgent(id),
    enabled: !!id,
  });
}
