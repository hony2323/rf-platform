const rawBase = import.meta.env.VITE_API_BASE_URL;

if (!rawBase) {
  throw new Error("Missing VITE_API_BASE_URL");
}

export const API_BASE_URL = rawBase.replace(/\/+$/, "");

export function apiUrl(path: string): string {
  return `${API_BASE_URL}${path.startsWith("/") ? path : `/${path}`}`;
}

export function wsUrl(path: string): string {
  const url = new URL(path.startsWith("/") ? path : `/${path}`, API_BASE_URL);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  return url.toString();
}
