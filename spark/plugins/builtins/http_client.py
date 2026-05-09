"""SSRF-hardened HTTP client plugin.

Every request passes through `spark.utils.net.validate_url` which:
  - rejects non-https (unless explicitly opted in)
  - rejects hostnames not in the allowlist (IDN-normalized)
  - resolves the hostname and rejects any private / loopback / link-local /
    cloud-metadata IP
  - returns the pinned IP to which the request will be made

The outbound httpx transport is pinned to the resolved IP with a custom
`Host` header — this defeats DNS rebinding because there is no second
resolution at connect time.
"""

from __future__ import annotations

from typing import Any, ClassVar, Literal

import httpx
from pydantic import BaseModel, Field

from spark.config.enums import Permission, Sensitivity


class HttpClientConfig(BaseModel):
    """Operator-edited defaults for the HTTP client plugin."""

    allow_hosts: list[str] = Field(default_factory=list)
    allow_http: bool = False
    allowed_methods: list[Literal["GET", "POST", "PUT", "DELETE"]] = Field(
        default_factory=lambda: ["GET"]
    )
    max_response_bytes: int = Field(default=5_000_000, ge=1, le=100_000_000)
    connect_timeout_seconds: float = Field(default=5.0, gt=0, le=60)
    read_timeout_seconds: float = Field(default=15.0, gt=0, le=300)
    user_agent: str = Field(default="spark-runtime/0.1", max_length=256)


class HttpRequestArgs(BaseModel):
    method: Literal["GET", "POST", "PUT", "DELETE"] = Field(
        default="GET",
        description="HTTP method. Must also be in the operator's allowed_methods.",
    )
    url: str = Field(
        description="Target URL. Host must be allowlisted; HTTPS unless allow_http is set.",
    )
    headers: dict[str, str] = Field(
        default_factory=dict,
        description="Plain headers (no secret values — use secret_headers for those).",
    )
    secret_headers: dict[str, str] = Field(
        default_factory=dict,
        description="Map of header name → secret name. The runtime substitutes the value at send time.",
    )
    body: str | None = Field(
        default=None,
        description="Raw request body. Mutually exclusive with `json`.",
    )
    json: dict[str, Any] | None = Field(
        default=None,
        description="JSON-encoded body. Sets Content-Type: application/json automatically.",
    )
    allow_hosts: list[str] = Field(
        default_factory=list,
        description="Per-call narrowing of allow_hosts (operator config wins).",
    )
    allow_http: bool = Field(
        default=False,
        description="Permit plain http:// for this request (operator config wins).",
    )
    max_response_bytes: int = Field(
        default=5_000_000,
        description="Response body cap. Operator config caps further.",
    )
    connect_timeout_seconds: float = Field(
        default=5.0,
        description="Connect timeout in seconds.",
    )
    read_timeout_seconds: float = Field(
        default=15.0,
        description="Read timeout in seconds.",
    )


class HttpResponse(BaseModel):
    status_code: int
    headers: dict[str, str]
    body: str
    truncated: bool
    content_type: str | None = None


class HttpClientPlugin:
    name: ClassVar[str] = "http_client"
    version: ClassVar[str] = "0.1.0"
    description: ClassVar[str] = "HTTPS client with SSRF defense and secret injection."
    input_schema: ClassVar[type[BaseModel]] = HttpRequestArgs
    output_schema: ClassVar[type[BaseModel]] = HttpResponse
    config_schema: ClassVar[type[BaseModel]] = HttpClientConfig
    required_permissions: ClassVar[frozenset[Permission]] = frozenset(
        {Permission.NET_HTTP, Permission.SECRETS_READ}
    )
    required_secrets: ClassVar[frozenset[str]] = frozenset()  # resolved dynamically via secret_headers
    sensitivity: ClassVar[Sensitivity] = Sensitivity.MODERATE
    filter_output_before_model: ClassVar[bool] = True
    needs_network: ClassVar[bool] = True

    async def execute(self, args: HttpRequestArgs, ctx: Any) -> HttpResponse:
        from spark.utils.net import HostPolicy, pin_dns, validate_url

        plugin_config = getattr(ctx, "plugin_config", {}) or {}
        allowed_methods = plugin_config.get("allowed_methods") or ["GET"]
        if args.method not in allowed_methods:
            raise PermissionError(
                f"http method {args.method!r} not in allowed_methods {allowed_methods}"
            )

        policy = HostPolicy.from_list(
            args.allow_hosts,
            allow_http=args.allow_http,
            allow_redirects=False,
        )
        target = validate_url(args.url, policy)

        headers = dict(args.headers)
        for header_name, secret_name in args.secret_headers.items():
            value = ctx.secrets.get(secret_name)
            if value is None:
                raise PermissionError(f"secret {secret_name!r} not injected into context")
            headers[header_name] = value
        headers.setdefault("User-Agent", plugin_config.get("user_agent", "spark-runtime/0.1"))

        timeout = httpx.Timeout(
            connect=args.connect_timeout_seconds,
            read=args.read_timeout_seconds,
            write=args.read_timeout_seconds,
            pool=args.connect_timeout_seconds,
        )
        with pin_dns(target):
            async with httpx.AsyncClient(
                timeout=timeout,
                follow_redirects=False,
                verify=True,
                trust_env=False,
            ) as client:
                async with client.stream(
                    args.method,
                    args.url,
                    headers=headers,
                    content=args.body,
                    json=args.json,
                ) as response:
                    body_bytes = bytearray()
                    truncated = False
                    async for chunk in response.aiter_bytes():
                        body_bytes.extend(chunk)
                        if len(body_bytes) > args.max_response_bytes:
                            truncated = True
                            body_bytes = body_bytes[: args.max_response_bytes]
                            break
                    try:
                        body_text = bytes(body_bytes).decode("utf-8")
                    except UnicodeDecodeError:
                        body_text = bytes(body_bytes).decode("utf-8", errors="replace")
                    return HttpResponse(
                        status_code=response.status_code,
                        headers={k: v for k, v in response.headers.items()},
                        body=body_text,
                        truncated=truncated,
                        content_type=response.headers.get("content-type"),
                    )
