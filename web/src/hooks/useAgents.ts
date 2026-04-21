import { useQuery } from "@tanstack/react-query";
import { getAgents } from "../api/agents";
import type { AgentResponse } from "../types/api";

export function useAgents() {
  return useQuery<AgentResponse[], Error>({
    queryKey: ["agents"],
    queryFn: getAgents,
  });
}
