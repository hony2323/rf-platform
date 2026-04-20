import { apiFetch } from "./client";
import type { AgentCreateRequest, AgentResponse, AgentStatusResponse } from "../types/api";

export function getAgents(): Promise<AgentResponse[]> {
  return apiFetch<AgentResponse[]>("/agents");
}

export function getAgent(id: string): Promise<AgentResponse> {
  return apiFetch<AgentResponse>(`/agents/${id}`);
}

export function getAgentStatus(id: string): Promise<AgentStatusResponse> {
  return apiFetch<AgentStatusResponse>(`/agents/${id}/status`);
}

export function createAgent(body: AgentCreateRequest): Promise<AgentResponse> {
  return apiFetch<AgentResponse>("/agents", {
    method: "POST",
    body: JSON.stringify(body),
  });
}
