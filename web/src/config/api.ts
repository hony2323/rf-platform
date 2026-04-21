const rawBase = import.meta.env.VITE_API_BASE_URL?.trim() ?? "";
const isAbsolute = /^https?:\/\//i.test(rawBase);

if (!rawBase && !import.meta.env.DEV) {
  throw new Error("Missing VITE_API_BASE_URL");
}

function normalizePath(path: string): string {
  return path.startsWith("/") ? path : `/${path}`;
}

function browserWsBase(): string {
  const url = new URL(window.location.origin);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  return url.origin;
}

export const API_BASE_URL = isAbsolute ? rawBase.replace(/\/+$/, "") : "";

export function apiUrl(path: string): string {
  const normalized = normalizePath(path);
  return API_BASE_URL ? `${API_BASE_URL}${normalized}` : normalized;
}

export function wsUrl(path: string): string {
  const normalized = normalizePath(path);

  if (API_BASE_URL) {
    const url = new URL(normalized, API_BASE_URL);
    url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
    return url.toString();
  }

  return `${browserWsBase()}${normalized}`;
}
