import { apiFetch } from "./client";
import type {
  DeleteAccountRequest,
  GoogleAuthRequest,
  LoginRequest,
  SignupRequest,
  UserResponse,
} from "../types/api";

export function login(email: string, password: string): Promise<UserResponse> {
  const body: LoginRequest = { email, password };
  return apiFetch<UserResponse>("/auth/login", {
    method: "POST",
    body: JSON.stringify(body),
    redirectOnUnauthorized: false,
  });
}

export function signup(email: string, password: string): Promise<UserResponse> {
  const body: SignupRequest = { email, password };
  return apiFetch<UserResponse>("/auth/signup", {
    method: "POST",
    body: JSON.stringify(body),
    redirectOnUnauthorized: false,
  });
}

export function loginWithGoogle(token: string): Promise<UserResponse> {
  const body: GoogleAuthRequest = { token };
  return apiFetch<UserResponse>("/auth/google", {
    method: "POST",
    body: JSON.stringify(body),
    redirectOnUnauthorized: false,
  });
}

export function logout(): Promise<void> {
  return apiFetch<void>("/auth/logout", { method: "POST" });
}

export function getMe(): Promise<UserResponse> {
  return apiFetch<UserResponse>("/me");
}

export function deleteAccount(password?: string): Promise<void> {
  const body: DeleteAccountRequest = { password };
  return apiFetch<void>("/me", {
    method: "DELETE",
    body: JSON.stringify(body),
    redirectOnUnauthorized: false,
  });
}
