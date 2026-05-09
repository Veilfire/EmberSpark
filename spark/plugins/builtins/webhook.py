"""Outbound webhook plugin.

Counterpart to the inbound trigger system. The agent uses this to push
notifications to external systems — Slack incoming webhooks, Zapier,
n8n, generic JSON consumers, downstream EmberSpark instances, etc.

Locked-down by design:

- The operator's plugin config supplies the **allowlisted target URLs**
  by host. The model can only POST to one of those hosts.
- Optional **HMAC-SHA256 signing** — if the operator configures a
  signing-key secret name, every outbound POST is signed with the
  industry-standard ``X-Spark-Signature-256: sha256=<hex>`` header so
  the receiver can verify authenticity. Use the same flavour as
  ``hmac_sha256`` inbound triggers — symmetric.
- **SSRF defense** — same gauntlet as the http_client plugin: IDN
  normalisation, DNS pinning, refusal of RFC1918 / loopback / cloud
  metadata IPs.

Tradeoff vs. http_client: deliberately narrower. No GET/PUT/DELETE.
Body is always a JSON object. The agent doesn't need 80% of HTTP
semantics to fire a webhook — they're confusing and risky in the
default case.
"""

from __future__ import annotations

import hmac
import json
from hashlib import sha256
from typing import Any, ClassVar, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field

from spark.config.enums import Permission, Sensitivity


class WebhookConfig(BaseModel):
    """Operator-locked allowlist + signing key for outbound webhooks."""

    model_config = ConfigDict(extra="forbid")

    #: Hosts (no scheme, no path) the agent is allowed to POST to. Empty
    #: list = plugin refuses every request. The operator must opt in
    #: per host.
    allow_hosts: list[str] = Field(default_factory=list)

    #: If set, every outbound request body is HMAC-SHA256 signed with
    #: this secret (looked up via the secret manager). Receivers verify
    #: via :func:`spark.utils.auth.verify_hmac_sha256`.
    signing_key_secret: str | None = Field(default=None, max_length=128)

    #: Header name for the signature. Default mirrors the inbound
    #: trigger expectation. Override to e.g. ``X-Hub-Signature-256`` if
    #: integrating with a system that already understands GitHub's name.
    signature_header: str = Field(default="X-Spark-Signature-256", max_length=128)

    #: Whether to allow plain HTTP. Default ``False`` (HTTPS only).
    allow_http: bool = False

    connect_timeout_seconds: float = Field(default=5.0, gt=0, le=60)
    read_timeout_seconds: float = Field(default=15.0, gt=0, le=120)
    max_body_bytes: int = Field(default=1_000_000, ge=1, le=10_000_000)
    user_agent: str = Field(default="spark-runtime/0.1", max_length=256)


class WebhookArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str = Field(
        min_length=1,
        max_length=2048,
        description="Target URL. Host must be in the operator's allow_hosts; HTTPS unless allow_http is set.",
    )
    payload: dict[str, Any] = Field(
        description="JSON-serializable object that becomes the request body.",
    )
    headers: dict[str, str] = Field(
        default_factory=dict,
        max_length=20,
        description=(
            "Optional non-secret headers. Merged with operator defaults; "
            "the signing header always wins if signing is on."
        ),
    )
    method: Literal["POST", "PUT"] = Field(
        default="POST",
        description="HTTP method. POST for most webhooks; PUT for idempotent integrations.",
    )


class WebhookResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status_code: int
    response_body: str
    signed: bool


class WebhookPlugin:
    name: ClassVar[str] = "webhook"
    version: ClassVar[str] = "0.1.0"
    description: ClassVar[str] = (
        "Outbound webhook with operator-allowlisted hosts, optional "
        "HMAC-SHA256 signing, and SSRF defense."
    )
    input_schema: ClassVar[type[BaseModel]] = WebhookArgs
    output_schema: ClassVar[type[BaseModel]] = WebhookResponse
    config_schema: ClassVar[type[BaseModel]] = WebhookConfig
    required_permissions: ClassVar[frozenset[Permission]] = frozenset(
        {Permission.NET_HTTP, Permission.SECRETS_READ}
    )
    required_secrets: ClassVar[frozenset[str]] = frozenset()  # operator picks
    sensitivity: ClassVar[Sensitivity] = Sensitivity.MODERATE
    filter_output_before_model: ClassVar[bool] = True
    needs_network: ClassVar[bool] = True

    async def execute(self, args: WebhookArgs, ctx: Any) -> WebhookResponse:
        from spark.utils.net import HostPolicy, pin_dns, validate_url

        cfg = getattr(ctx, "plugin_config", {}) or {}
        allow_hosts = list(cfg.get("allow_hosts") or [])
        if not allow_hosts:
            raise PermissionError(
                "webhook: operator has not allowlisted any hosts; "
                "edit the plugin config and add at least one"
            )
        signing_key_secret = cfg.get("signing_key_secret")
        signature_header = cfg.get("signature_header") or "X-Spark-Signature-256"
        allow_http = bool(cfg.get("allow_http", False))
        connect_timeout = float(cfg.get("connect_timeout_seconds") or 5.0)
        read_timeout = float(cfg.get("read_timeout_seconds") or 15.0)
        max_body_bytes = int(cfg.get("max_body_bytes") or 1_000_000)
        user_agent = cfg.get("user_agent") or "spark-runtime/0.1"

        # SSRF gauntlet — same as http_client.
        policy = HostPolicy.from_list(
            allow_hosts, allow_http=allow_http, allow_redirects=False
        )
        target = validate_url(args.url, policy)

        body_bytes = json.dumps(args.payload, separators=(",", ":")).encode("utf-8")
        if len(body_bytes) > max_body_bytes:
            raise PermissionError(
                f"webhook: body {len(body_bytes)} bytes exceeds cap {max_body_bytes}"
            )

        headers = {k: v for k, v in args.headers.items() if _is_safe_header(k)}
        headers.setdefault("Content-Type", "application/json")
        headers.setdefault("User-Agent", user_agent)

        signed = False
        if signing_key_secret:
            secrets = getattr(ctx, "secrets", {}) or {}
            secret_value = secrets.get(signing_key_secret)
            if not secret_value:
                raise PermissionError(
                    f"webhook: signing_key_secret {signing_key_secret!r} not "
                    "injected into context — operator must include it in the "
                    "agent's required_secrets"
                )
            digest = hmac.new(
                secret_value.encode("utf-8"), body_bytes, sha256
            ).hexdigest()
            headers[signature_header] = f"sha256={digest}"
            signed = True

        timeout = httpx.Timeout(
            connect=connect_timeout,
            read=read_timeout,
            write=read_timeout,
            pool=connect_timeout,
        )
        with pin_dns(target):
            async with httpx.AsyncClient(
                timeout=timeout,
                follow_redirects=False,
                verify=True,
                trust_env=False,
            ) as client:
                resp = await client.request(
                    args.method, args.url, headers=headers, content=body_bytes
                )
                text = resp.text[:8192]
        return WebhookResponse(
            status_code=resp.status_code, response_body=text, signed=signed
        )


def _is_safe_header(name: str) -> bool:
    """Block headers that could let the model rewrite request semantics."""
    n = name.lower().strip()
    if not n:
        return False
    # Refuse hop-by-hop and request-line-altering headers.
    if n in {
        "host",
        "content-length",
        "content-encoding",
        "transfer-encoding",
        "connection",
        "expect",
        "trailer",
        "upgrade",
    }:
        return False
    if "\r" in name or "\n" in name:
        return False
    return True
