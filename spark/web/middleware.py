"""Custom Starlette middlewares: CIDR allowlist + rate limiter."""

from __future__ import annotations

import ipaddress
import time
from collections import defaultdict, deque
from typing import Awaitable, Callable, Deque

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response


class CidrAllowlistMiddleware(BaseHTTPMiddleware):
    """Reject any request whose source IP is outside the configured CIDR set.

    - An empty CIDR list means "no filter" (caller should not install the
      middleware at all in that case — the factory handles that).
    - If `trusted_proxies` is non-empty and the request arrives from one of
      them, we honor the leftmost `X-Forwarded-For` entry. Otherwise we use
      the raw client address.
    - Requests to `/api/health` are ALWAYS allowed so external health checks
      can reach the server without being in the allowlist.
    """

    def __init__(
        self,
        app,
        *,
        allowed_cidrs: list[str],
        trusted_proxies: list[str] | None = None,
    ) -> None:
        super().__init__(app)
        self._networks = [
            ipaddress.ip_network(cidr, strict=False) for cidr in allowed_cidrs
        ]
        self._trusted_proxies = frozenset(trusted_proxies or [])

    def _client_ip(self, request: Request) -> str | None:
        """Return the source IP, honoring XFF only from trusted proxies.

        The leftmost XFF entry is parsed as a real IP address; if parsing
        fails we fall back to the raw peer address instead of trusting a
        malformed candidate.
        """
        raw = request.client.host if request.client else None
        if raw and raw in self._trusted_proxies:
            xff = request.headers.get("x-forwarded-for", "")
            if xff:
                candidate = xff.split(",")[0].strip()
                if candidate.startswith("[") and candidate.endswith("]"):
                    candidate = candidate[1:-1]
                try:
                    ipaddress.ip_address(candidate)
                    return candidate
                except ValueError:
                    return raw
        return raw

    def _is_allowed(self, ip_str: str | None) -> bool:
        if ip_str is None:
            return False
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            return False
        return any(ip in net for net in self._networks)

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        if request.url.path == "/api/health":
            return await call_next(request)
        ip = self._client_ip(request)
        if not self._is_allowed(ip):
            return JSONResponse(
                {"detail": f"source ip {ip!r} not in allowlist"},
                status_code=403,
            )
        return await call_next(request)


class SimpleRateLimitMiddleware(BaseHTTPMiddleware):
    """Minimal sliding-window rate limiter, per client IP.

    Intended as a second-line defense; the Security Center already enforces
    per-route gates. If you need serious throttling, put a reverse proxy in
    front.
    """

    def __init__(self, app, *, requests_per_minute: int) -> None:
        super().__init__(app)
        self._limit = requests_per_minute
        self._window = 60.0
        self._buckets: dict[str, Deque[float]] = defaultdict(deque)

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        if self._limit <= 0:
            return await call_next(request)
        now = time.monotonic()
        ip = request.client.host if request.client else "unknown"
        bucket = self._buckets[ip]
        while bucket and now - bucket[0] > self._window:
            bucket.popleft()
        if len(bucket) >= self._limit:
            return JSONResponse(
                {"detail": "rate limit exceeded"},
                status_code=429,
            )
        bucket.append(now)
        return await call_next(request)


_BASE_CSP = (
    "default-src 'self'; "
    "img-src 'self' data:; "
    "style-src 'self' 'unsafe-inline'; "
    "script-src 'self'; "
    "connect-src 'self' ws: wss:; "
    "font-src 'self' data:; "
    "object-src 'none'; "
    "base-uri 'self'; "
    "form-action 'self'; "
    "frame-ancestors 'none'"
)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Inject conservative security headers on every response.

    HSTS is only set when the request arrived via HTTPS (direct TLS or
    behind a proxy that forwards ``X-Forwarded-Proto``). We never set HSTS
    on a loopback/http deployment because that would cause the browser to
    refuse plaintext localhost once it caches the header.
    """

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        response.headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")
        response.headers.setdefault("Cross-Origin-Embedder-Policy", "require-corp")
        response.headers.setdefault(
            "Permissions-Policy",
            "geolocation=(), microphone=(), camera=(), usb=(), payment=()",
        )
        response.headers.setdefault("Content-Security-Policy", _BASE_CSP)

        scheme = request.url.scheme
        forwarded_proto = request.headers.get("x-forwarded-proto", "").lower()
        if scheme == "https" or forwarded_proto == "https":
            response.headers.setdefault(
                "Strict-Transport-Security",
                "max-age=31536000; includeSubDomains",
            )
        return response
