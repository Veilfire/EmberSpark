// Typed API client. All endpoints go through here so we have one place to
// handle auth, errors, and CSRF.

export class ApiError extends Error {
  constructor(public status: number, message: string, public detail?: unknown) {
    super(message);
  }
}

async function request<T>(
  path: string,
  options: RequestInit = {}
): Promise<T> {
  const response = await fetch(path, {
    credentials: "include",
    headers: {
      "content-type": "application/json",
      ...(options.headers || {}),
    },
    ...options,
  });
  if (!response.ok) {
    // Session expired or never authenticated — bounce to login.
    // Skip this for the /api/auth/ routes themselves so the login
    // page can handle 401 without an infinite redirect.
    if (
      response.status === 401 &&
      !path.startsWith("/api/auth/")
    ) {
      window.location.href = "/login";
      // Never resolves — the browser navigates away.
      return new Promise<never>(() => {});
    }
    const text = await response.text();
    let detail: unknown = text;
    try {
      detail = JSON.parse(text);
    } catch {
      /* keep raw */
    }
    throw new ApiError(response.status, `${response.status} ${response.statusText}`, detail);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: "POST", body: body ? JSON.stringify(body) : undefined }),
  put: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: "PUT", body: body ? JSON.stringify(body) : undefined }),
  del: <T>(path: string) => request<T>(path, { method: "DELETE" }),
};

export function sseConnect(
  path: string,
  handlers: { onMessage?: (data: unknown) => void; onError?: (e: Event) => void }
): () => void {
  const source = new EventSource(path, { withCredentials: true });
  source.onmessage = (event) => {
    try {
      handlers.onMessage?.(JSON.parse(event.data));
    } catch {
      handlers.onMessage?.(event.data);
    }
  };
  if (handlers.onError) source.onerror = handlers.onError;
  return () => source.close();
}
