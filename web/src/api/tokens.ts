import { apiFetch } from "./client";
import type { TokenCreateRequest, TokenCreateResponse, TokenResponse } from "../types/api";

export function getAgentTokens(agentId: string): Promise<TokenResponse[]> {
  return apiFetch<TokenResponse[]>(`/agents/${agentId}/tokens`);
}

export function createAgentToken(
  agentId: string,
  label: string | null,
): Promise<TokenCreateResponse> {
  const body: TokenCreateRequest = { label };
  return apiFetch<TokenCreateResponse>(`/agents/${agentId}/tokens`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function revokeAgentToken(
  agentId: string,
  tokenId: string,
): Promise<TokenResponse> {
  return apiFetch<TokenResponse>(`/agents/${agentId}/tokens/${tokenId}/revoke`, {
    method: "POST",
  });
}
