export interface LoginRequest {
  email: string;
  password: string;
}

export interface SignupRequest {
  email: string;
  password: string;
}

export interface GoogleAuthRequest {
  token: string;
}

export interface DeleteAccountRequest {
  password?: string;
}

export interface UserResponse {
  id: string;
  email: string;
}

export interface AgentResponse {
  id: string;
  name: string;
  stable_node_id: string;
}

export interface AgentCreateRequest {
  name: string;
  stable_node_id: string;
}

export interface AgentStatusResponse {
  agent_id: string;
  online: boolean;
  session_id: string | null;
  last_heartbeat_at: string | null;
  last_status: unknown | null;
}

export interface TokenCreateRequest {
  label: string | null;
}

export interface TokenResponse {
  id: string;
  label: string | null;
  created_at: string;
}

export interface TokenCreateResponse extends TokenResponse {
  token: string;
}
