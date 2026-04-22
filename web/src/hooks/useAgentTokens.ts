import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createAgentToken, deleteAgentToken, getAgentTokens, revokeAgentToken } from "../api/tokens";
import type { TokenCreateResponse, TokenResponse } from "../types/api";

export function useAgentTokens(agentId: string) {
  return useQuery<TokenResponse[], Error>({
    queryKey: ["agentTokens", agentId],
    queryFn: () => getAgentTokens(agentId),
  });
}

export function useCreateAgentToken(agentId: string) {
  const queryClient = useQueryClient();
  return useMutation<TokenCreateResponse, Error, string | null>({
    mutationFn: (label) => createAgentToken(agentId, label),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["agentTokens", agentId] });
    },
  });
}

export function useRevokeAgentToken(agentId: string) {
  const queryClient = useQueryClient();
  return useMutation<TokenResponse, Error, string>({
    mutationFn: (tokenId) => revokeAgentToken(agentId, tokenId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["agentTokens", agentId] });
    },
  });
}

export function useDeleteAgentToken(agentId: string) {
  const queryClient = useQueryClient();
  return useMutation<TokenResponse, Error, string>({
    mutationFn: (tokenId) => deleteAgentToken(agentId, tokenId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["agentTokens", agentId] });
    },
  });
}
