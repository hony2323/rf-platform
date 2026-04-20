export class ApiError extends Error {
  constructor(
    public readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export class UnauthorizedError extends ApiError {
  constructor() {
    super(401, "Unauthorized");
    this.name = "UnauthorizedError";
  }
}

let redirectingToLogin = false;

function redirectToLogin(): void {
  if (redirectingToLogin) return;
  redirectingToLogin = true;
  window.location.replace("/login");
}

function normalizeHeaders(init: HeadersInit | undefined): Record<string, string> {
  if (!init) return {};
  if (init instanceof Headers) {
    const out: Record<string, string> = {};
    init.forEach((value, key) => { out[key] = value; });
    return out;
  }
  if (Array.isArray(init)) {
    return Object.fromEntries(init) as Record<string, string>;
  }
  return { ...init };
}

async function parseBody<T>(res: Response): Promise<T> {
  if (res.headers.get("Content-Length") === "0") return undefined as T;
  const text = await res.text();
  if (text === "") return undefined as T;
  return JSON.parse(text) as T;
}

async function extractErrorMessage(res: Response): Promise<string> {
  try {
    const text = await res.text();
    if (text !== "") {
      const body: unknown = JSON.parse(text);
      if (body !== null && typeof body === "object") {
        const b = body as Record<string, unknown>;
        if (typeof b.detail === "string") return b.detail;
        if (typeof b.message === "string") return b.message;
      }
    }
  } catch {
    // ignore parse errors
  }
  return res.statusText;
}

export async function apiFetch<T>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
  const caller = normalizeHeaders(init.headers);
  const auto: Record<string, string> =
    typeof init.body === "string" && !("content-type" in caller) && !("Content-Type" in caller)
      ? { "Content-Type": "application/json" }
      : {};

  const res = await fetch(path, {
    ...init,
    credentials: "include",
    headers: { ...auto, ...caller },
  });

  if (res.status === 401) {
    redirectToLogin();
    throw new UnauthorizedError();
  }

  if (!res.ok) {
    throw new ApiError(res.status, await extractErrorMessage(res));
  }

  return parseBody<T>(res);
}
