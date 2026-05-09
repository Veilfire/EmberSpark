// Typed API client. All endpoints go through here so we have one place to
// handle auth, errors, and CSRF.
export class ApiError extends Error {
    status;
    detail;
    constructor(status, message, detail) {
        super(message);
        this.status = status;
        this.detail = detail;
    }
}
async function request(path, options = {}) {
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
        if (response.status === 401 &&
            !path.startsWith("/api/auth/")) {
            window.location.href = "/login";
            // Never resolves — the browser navigates away.
            return new Promise(() => { });
        }
        const text = await response.text();
        let detail = text;
        try {
            detail = JSON.parse(text);
        }
        catch {
            /* keep raw */
        }
        throw new ApiError(response.status, `${response.status} ${response.statusText}`, detail);
    }
    if (response.status === 204)
        return undefined;
    return (await response.json());
}
export const api = {
    get: (path) => request(path),
    post: (path, body) => request(path, { method: "POST", body: body ? JSON.stringify(body) : undefined }),
    put: (path, body) => request(path, { method: "PUT", body: body ? JSON.stringify(body) : undefined }),
    del: (path) => request(path, { method: "DELETE" }),
};
export function sseConnect(path, handlers) {
    const source = new EventSource(path, { withCredentials: true });
    source.onmessage = (event) => {
        try {
            handlers.onMessage?.(JSON.parse(event.data));
        }
        catch {
            handlers.onMessage?.(event.data);
        }
    };
    if (handlers.onError)
        source.onerror = handlers.onError;
    return () => source.close();
}
