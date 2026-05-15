"""Shared HTTP-plugin helpers.

Every network-facing plugin in this batch (calendar, imap_reader,
slack, weather, wikipedia, maps, cloud_drive — plus the existing
home_assistant) talks to a remote service over HTTP/S with a vault-
stored credential and needs the same three things:

1. **Build an httpx client** with the operator-configured timeouts +
   ssl verification + no env-trust + no follow-redirects (SSRF
   hardening defaults).
2. **Resolve a token / password** from ``ctx.secrets``, raising
   :class:`SparkError(SECRET_NOT_FOUND)` with the right ``secret_name``
   in ``detail`` so the Failure Inspector deep-links to ``/secrets``
   correctly.
3. **Classify a network failure** — ``httpx.ConnectError`` to an
   RFC1918 / loopback host becomes ``SparkError(URL_PRIVATE_IP)`` so
   the inspector deep-links to the internal-IP grant flow; everything
   else becomes ``URL_DENIED``.

Six plugins re-implementing this on their own would be churn. One
helper module keeps the shape consistent + matches what the Failure
Inspector catalogue expects.
"""

from __future__ import annotations

import ipaddress
from typing import Any
from urllib.parse import urlparse

import httpx

from spark.errors import ErrorCode, SparkError


def build_client(cfg: dict[str, Any]) -> httpx.AsyncClient:
    """SSRF-hardened ``httpx.AsyncClient`` from the operator's config.

    Reads the standard timeout / SSL config field names every plugin in
    this batch uses:

    - ``connect_timeout_seconds`` (default 5.0)
    - ``read_timeout_seconds`` (default 15.0)
    - ``verify_ssl`` (default True)

    Hardcoded for safety regardless of cfg:

    - ``trust_env=False`` — never reads HTTP_PROXY / SSL_CERT_FILE / etc.
    - ``follow_redirects=False`` — redirect to a metadata IP would
      bypass the SSRF check; redirects are an explicit, plugin-side
      decision when needed.
    """
    timeout = httpx.Timeout(
        connect=float(cfg.get("connect_timeout_seconds") or 5.0),
        read=float(cfg.get("read_timeout_seconds") or 15.0),
        write=5.0,
        pool=5.0,
    )
    return httpx.AsyncClient(
        timeout=timeout,
        verify=bool(cfg.get("verify_ssl", True)),
        trust_env=False,
        follow_redirects=False,
    )


def resolve_secret(
    cfg: dict[str, Any],
    *,
    config_key: str,
    default_secret_name: str,
    plugin_name: str,
    ctx: Any,
) -> str:
    """Resolve a vault secret named by ``cfg[config_key]``.

    Raises ``SparkError(SECRET_NOT_FOUND)`` with the right shape:

    - ``detail.plugin`` — for the catalogue to scope the option
    - ``detail.secret_name`` — for the inspector's "Populate the
      secret" deep-link

    Caller passes the canonical ``ctx`` (Spark's ``ToolContext``);
    ``getattr(..., "secrets", {})`` is the defensive fallback every
    builtin plugin uses.
    """
    secret_name = (cfg.get(config_key) or default_secret_name).strip()
    secrets = getattr(ctx, "secrets", {}) or {}
    value = secrets.get(secret_name) if isinstance(secrets, dict) else None
    if not value:
        raise SparkError(
            ErrorCode.SECRET_NOT_FOUND,
            f"{plugin_name}: secret {secret_name!r} not injected",
            detail={
                "plugin": plugin_name,
                "secret_name": secret_name,
            },
        )
    return str(value)


def hostname_of(url: str) -> str:
    return (urlparse(url).hostname or "").strip()


def looks_private(host: str) -> bool:
    """Heuristic: is the host a private / loopback / link-local IP.

    Used to map a generic ``httpx.ConnectError`` to ``URL_PRIVATE_IP``
    vs ``URL_DENIED`` so the Failure Inspector can offer the right
    tuning option. Hostnames (not literal IPs) return ``False`` —
    that's fine; the real allowlist is enforced by
    ``spark.utils.net.HostPolicy`` upstream, and a misclassified
    error still gets a sensible URL-denied option.
    """
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_unspecified
    )


def classify_connect_error(
    exc: Exception,
    *,
    url: str,
    plugin_name: str,
) -> SparkError:
    """Map a network exception to a ``SparkError`` with stable code.

    Use inside an ``except httpx.RequestError`` block. Returns the
    appropriate ``SparkError``; the caller re-raises::

        try:
            await client.get(...)
        except httpx.RequestError as exc:
            raise classify_connect_error(exc, url=url, plugin_name="calendar") from exc
    """
    host = hostname_of(url)
    if isinstance(exc, httpx.ConnectError):
        code = (
            ErrorCode.URL_PRIVATE_IP
            if looks_private(host)
            else ErrorCode.URL_DENIED
        )
        return SparkError(
            code,
            f"{plugin_name}: cannot reach {host or url}: {exc}",
            detail={"plugin": plugin_name, "host": host},
        )
    return SparkError(
        ErrorCode.PLUGIN_RAISED,
        f"{plugin_name}: request failed: {exc}",
        detail={"plugin": plugin_name},
    )


def cap_bytes(body: str, *, max_bytes: int) -> tuple[str, bool]:
    """Return ``(possibly-truncated body, truncated_flag)``.

    Every plugin in this batch caps response bodies so a giant HA
    state-dump / wikipedia article / IMAP body doesn't blow the prompt
    window. Single helper keeps the cap semantics consistent.
    """
    if len(body) <= max_bytes:
        return body, False
    return body[:max_bytes], True
