// fetch wrapper that injects the Cognito JWT (Spec/09 §4 L1 §2.5).
import { getJwt } from "./auth";

// Resolve API base URL in this priority order:
//   1. window.__LABORAID_CONFIG__.apiEndpoint (set by main.tsx after /config.json fetch)
//   2. VITE_API_BASE_URL build-time env var (for local dev)
//   3. empty string (only for unit tests where requests are stubbed)
// The runtime-config path lets the same bundle target dev/prod without a rebuild.
declare global {
  interface Window {
    __LABORAID_CONFIG__?: { apiEndpoint?: string };
  }
}

// Resolve lazily inside request() — main.tsx sets window.__LABORAID_CONFIG__
// AFTER this module has been imported, so reading it at module-init time
// captures undefined and BASE stays "" forever. Evaluating on every call is
// cheap and fixes the race where api requests went to the SPA origin.
function baseUrl(): string {
  const r =
    typeof window !== "undefined" ? window.__LABORAID_CONFIG__?.apiEndpoint : undefined;
  return (r || import.meta.env.VITE_API_BASE_URL || "").replace(/\/$/, "");
}

async function request<T>(
  method: string,
  path: string,
  body?: unknown,
): Promise<T> {
  const jwt = await getJwt();
  const res = await fetch(`${baseUrl()}${path}`, {
    method,
    headers: {
      "Content-Type": "application/json",
      ...(jwt ? { Authorization: `Bearer ${jwt}` } : {}),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) {
    throw new ApiError(res.status, await res.text());
  }
  return (await res.json()) as T;
}

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export const api = {
  get: <T>(path: string) => request<T>("GET", path),
  post: <T>(path: string, body?: unknown) => request<T>("POST", path, body),
  put: <T>(path: string, body?: unknown) => request<T>("PUT", path, body),
  patch: <T>(path: string, body?: unknown) => request<T>("PATCH", path, body),
};
