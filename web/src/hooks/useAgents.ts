import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { deleteAgent, getAgent, getAgents } from "../api/agents";
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

export function useDeleteAgent() {
  const queryClient = useQueryClient();
  return useMutation<void, Error, string>({
    mutationFn: (agentId) => deleteAgent(agentId),
    onSuccess: (_, agentId) => {
      void queryClient.invalidateQueries({ queryKey: ["agents"] });
      void queryClient.invalidateQueries({ queryKey: ["agentTokens", agentId] });
      queryClient.removeQueries({ queryKey: ["agent", agentId] });
      queryClient.removeQueries({ queryKey: ["agents", agentId] });
      queryClient.removeQueries({ queryKey: ["agentStatus", agentId] });
    },
  });
}
