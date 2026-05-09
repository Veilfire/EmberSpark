"""HTTP tool with per-host method matrices + optional readability extraction.

`http_tool` is the richer sibling of `http_client`. Where `http_client`
exposes a single flat ``allow_hosts`` list and one ``allowed_methods`` list
that applies to every host, `http_tool` lets the operator configure a
**list of rules**, each one:

- pinned to one hostname
- with its own allowed HTTP methods (GET / POST / PUT / DELETE / PATCH / HEAD)
- with its own optional response cap, connect/read timeouts, and plaintext
  override
- with optional readable-content extraction on GET HTML responses

Same SSRF defense as `http_client`: IDN normalization, RFC1918/loopback/
metadata blocklist, IP-pinned transport, no redirects.
"""

from __future__ import annotations

from typing import Any, ClassVar, Literal
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, ConfigDict, Field, model_validator

from spark.config.enums import Permission, Sensitivity

Method = Literal["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD"]


class HttpToolHostRule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    host: str = Field(min_length=1, max_length=253)
    allowed_methods: list[Method] = Field(default_factory=lambda: ["GET"])
    allow_http: bool = False
    max_response_bytes: int | None = Field(default=None, ge=1, le=100_000_000)
    connect_timeout_seconds: float | None = Field(default=None, gt=0, le=60)
    read_timeout_seconds: float | None = Field(default=None, gt=0, le=300)
    extract_main_content: bool = False
    note: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def _host_lowercase(self) -> HttpToolHostRule:
        # Normalize operator input so rule matching later is straightforward.
        object.__setattr__(self, "host", self.host.strip().lower())
        return self


def _default_http_tool_rules() -> list[HttpToolHostRule]:
    # Read-only browsing of any HTTPS host with a 5MB cap and main-content
    # extraction. Operators tighten this with named-host rules in production;
    # the default lets a fresh agent fetch articles immediately without
    # editing config first.
    return [
        HttpToolHostRule(
            host="*",
            allowed_methods=["GET"],
            max_response_bytes=5_000_000,
            extract_main_content=True,
            note="default open GET rule — replace with named hosts for prod",
        )
    ]


class HttpToolConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rules: list[HttpToolHostRule] = Field(default_factory=_default_http_tool_rules)
    default_max_response_bytes: int = Field(default=10_000_000, ge=1, le=100_000_000)
    default_connect_timeout_seconds: float = Field(default=5.0, gt=0, le=60)
    default_read_timeout_seconds: float = Field(default=15.0, gt=0, le=300)
    user_agent: str = Field(default="spark-runtime-http-tool/0.1", max_length=256)


class HttpToolArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    method: Method = Field(
        description="HTTP method. Must be in the matched host rule's allowed_methods.",
    )
    url: str = Field(
        description="Target URL. Host must match a rule entry in the operator config.",
    )
    headers: dict[str, str] = Field(
        default_factory=dict,
        description="Plain headers (no secret values — use secret_headers for those).",
    )
    secret_headers: dict[str, str] = Field(
        default_factory=dict,
        description="Map of header name → secret name (resolved from ctx.secrets).",
    )
    body: str | None = Field(
        default=None,
        description="Raw request body. Mutually exclusive with `json`.",
    )
    json_body: Any | None = Field(
        default=None,
        alias="json",
        description="JSON body — auto-encodes and sets Content-Type: application/json.",
    )
    extract_main_content: bool | None = Field(
        default=None,
        description="Per-call override of the rule's extract_main_content default (HTML → readable text).",
    )


class HttpToolResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str
    status: int
    headers: dict[str, str]
    body_text: str
    content_type: str | None = None
    main_content: str | None = None
    truncated: bool = False


class HttpToolPlugin:
    name: ClassVar[str] = "http_tool"
    version: ClassVar[str] = "0.1.0"
    description: ClassVar[str] = (
        "Rich HTTP client with per-host method matrices and optional readable-"
        "content extraction. The flexible alternative to http_client."
    )
    input_schema: ClassVar[type[BaseModel]] = HttpToolArgs
    output_schema: ClassVar[type[BaseModel]] = HttpToolResponse
    config_schema: ClassVar[type[BaseModel]] = HttpToolConfig
    required_permissions: ClassVar[frozenset[Permission]] = frozenset(
        {Permission.NET_HTTP, Permission.SECRETS_READ}
    )
    required_secrets: ClassVar[frozenset[str]] = frozenset()
    sensitivity: ClassVar[Sensitivity] = Sensitivity.MODERATE
    filter_output_before_model: ClassVar[bool] = True
    needs_network: ClassVar[bool] = True

    async def execute(self, args: HttpToolArgs, ctx: Any) -> HttpToolResponse:
        from spark.utils.net import HostPolicy, pin_dns, validate_url

        cfg = getattr(ctx, "plugin_config", {}) or {}
        rules_raw: list[dict[str, Any]] = cfg.get("rules") or []
        default_max_bytes = int(cfg.get("default_max_response_bytes") or 10_000_000)
        default_connect = float(cfg.get("default_connect_timeout_seconds") or 5.0)
        default_read = float(cfg.get("default_read_timeout_seconds") or 15.0)
        user_agent = cfg.get("user_agent") or "spark-runtime-http-tool/0.1"

        # Find the rule matching the URL's hostname.
        parsed = urlparse(args.url)
        requested_host = (parsed.hostname or "").strip().lower()
        if not requested_host:
            raise PermissionError("http_tool: URL has no hostname")

        rule = None
        wildcard = None
        for r in rules_raw:
            rule_host = (r.get("host") or "").strip().lower()
            if rule_host == requested_host:
                rule = r
                break
            if rule_host == "*" and wildcard is None:
                wildcard = r
        if rule is None:
            rule = wildcard
        if rule is None:
            raise PermissionError(
                f"http_tool: host {requested_host!r} not in any rule; "
                "operator must add a rule entry (or a '*' wildcard) to permit this host"
            )

        allowed_methods = rule.get("allowed_methods") or ["GET"]
        if args.method not in allowed_methods:
            raise PermissionError(
                f"http_tool: method {args.method!r} not allowed on {requested_host!r} "
                f"(allowed: {sorted(allowed_methods)})"
            )

        allow_http = bool(rule.get("allow_http") or False)
        max_bytes = int(rule.get("max_response_bytes") or default_max_bytes)
        connect_timeout = float(rule.get("connect_timeout_seconds") or default_connect)
        read_timeout = float(rule.get("read_timeout_seconds") or default_read)
        rule_extract = bool(rule.get("extract_main_content") or False)
        extract = (
            args.extract_main_content if args.extract_main_content is not None else rule_extract
        )

        policy = HostPolicy.from_list(
            [requested_host], allow_http=allow_http, allow_redirects=False
        )
        target = validate_url(args.url, policy)

        # Assemble headers: start with model-supplied, then inject secret
        # headers by name from ctx.secrets, then set Host and User-Agent.
        headers = dict(args.headers)
        secrets = getattr(ctx, "secrets", {}) or {}
        for header_name, secret_name in args.secret_headers.items():
            value = secrets.get(secret_name)
            if value is None:
                raise PermissionError(
                    f"http_tool: secret {secret_name!r} not injected into context"
                )
            headers[header_name] = value
        headers.setdefault("User-Agent", user_agent)

        timeout = httpx.Timeout(
            connect=connect_timeout,
            read=read_timeout,
            write=read_timeout,
            pool=connect_timeout,
        )
        with pin_dns(target):
            async with httpx.AsyncClient(
                timeout=timeout, follow_redirects=False, verify=True, trust_env=False
            ) as client, client.stream(
                args.method,
                args.url,
                headers=headers,
                content=args.body,
                json=args.json_body,
            ) as response:
                body_bytes = bytearray()
                truncated = False
                async for chunk in response.aiter_bytes():
                    body_bytes.extend(chunk)
                    if len(body_bytes) > max_bytes:
                        truncated = True
                        body_bytes = body_bytes[:max_bytes]
                        break
                try:
                    body_text = bytes(body_bytes).decode("utf-8")
                except UnicodeDecodeError:
                    body_text = bytes(body_bytes).decode("utf-8", errors="replace")
                content_type = response.headers.get("content-type")
                response_headers = {k: v for k, v in response.headers.items()}
                status_code = response.status_code

        main_content: str | None = None
        if (
            extract
            and args.method == "GET"
            and content_type
            and "html" in content_type.lower()
        ):
            main_content = _extract_main_content(body_text)

        return HttpToolResponse(
            url=args.url,
            status=status_code,
            headers=response_headers,
            body_text=body_text,
            content_type=content_type,
            main_content=main_content,
            truncated=truncated,
        )


def _extract_main_content(html: str) -> str | None:
    """Return the article body from an HTML page, or None on failure.

    Uses ``trafilatura`` if installed. If not, returns ``None`` and the
    caller can fall back to the raw body.
    """
    try:
        import trafilatura  # type: ignore[import-not-found]
    except ImportError:
        return None
    try:
        extracted = trafilatura.extract(
            html,
            include_comments=False,
            include_tables=False,
            no_fallback=False,
        )
    except Exception:
        return None
    if not extracted:
        return None
    # Hard cap the extracted text so the model context stays bounded even
    # on enormous articles.
    return extracted[:50_000]
