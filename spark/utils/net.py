"""SSRF defense utilities.

The SSRF defense has three layers:

1. **Hostname allowlist** — checked before any network call. Hostnames are
   normalized to lowercase and IDN-decoded before comparison.
2. **IP validation** — every resolved IP must not be private, loopback,
   link-local, multicast, reserved, or a cloud metadata address.
3. **IP pinning transport** — the outbound HTTPS request is made against the
   resolved IP directly, with the original hostname in the `Host` header. This
   defeats DNS rebinding because there is no second resolution at connect time.

See ``spark.plugins.builtins.http_client`` for the httpx wiring.
"""

from __future__ import annotations

import contextlib
import ipaddress
import socket
from collections.abc import Iterator
from dataclasses import dataclass
from urllib.parse import urlparse

# Cloud metadata endpoints + link-local; both IPv4 and IPv6.
_BLOCKED_EXACT = frozenset(
    {
        "169.254.169.254",   # AWS / GCP / Azure IMDS
        "100.100.100.200",   # Alibaba Cloud
        "fd00:ec2::254",     # AWS IPv6 metadata
        "::1",
        "0.0.0.0",           # noqa: S104  — the address itself is unsafe as target
    }
)


from spark.errors import ErrorCode, SparkError


class UrlDenied(SparkError, PermissionError):
    """Raised when a URL fails the SSRF gauntlet.

    Carries a ``SparkError`` code (defaults to ``URL_DENIED``) so the
    engine can surface a structured error payload to the model. Callers
    can pass a more specific code (``URL_METADATA_BLOCKED``,
    ``URL_PRIVATE_IP``, ``URL_IDN_INVALID``) when they know the
    specific failure mode.
    """

    def __init__(
        self,
        message: str,
        *,
        code: ErrorCode = ErrorCode.URL_DENIED,
        detail: dict | None = None,
    ) -> None:
        SparkError.__init__(self, code, message, detail=detail or {})


@dataclass(frozen=True)
class HostPolicy:
    """Host allowlist for outbound network calls."""

    allow_hosts: frozenset[str]
    allow_http: bool = False  # https-only by default
    allow_redirects: bool = False

    @classmethod
    def from_list(
        cls,
        hosts: list[str] | None,
        *,
        allow_http: bool = False,
        allow_redirects: bool = False,
    ) -> "HostPolicy":
        normalized = frozenset(_normalize_host(h) for h in (hosts or []))
        return cls(
            allow_hosts=normalized,
            allow_http=allow_http,
            allow_redirects=allow_redirects,
        )


@dataclass(frozen=True)
class ResolvedTarget:
    """Result of a validated URL resolution."""

    url: str
    scheme: str
    host: str
    port: int
    ip: str


def _normalize_host(host: str) -> str:
    """Normalize a hostname for allowlist comparison.

    - Strip brackets from IPv6 literals.
    - Lowercase (ASCII case-folding only).
    - Encode via IDNA so homoglyph labels (Cyrillic ``а`` in ``аpple.com``)
      become their canonical punycode form.
    - **Fail closed** if the host contains non-ASCII after normalization or
      IDNA encoding raises. The caller treats failure as ``UrlDenied``.
    """
    host = host.strip()
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    host = host.lower()

    try:
        encoded = host.encode("idna").decode("ascii")
    except (UnicodeError, UnicodeDecodeError) as exc:
        raise UrlDenied(f"host {host!r} failed IDNA normalization: {exc}") from exc

    if not encoded.isascii():
        raise UrlDenied(f"host {host!r} contains non-ASCII after IDNA normalization")
    return encoded


def validate_url(url: str, policy: HostPolicy) -> ResolvedTarget:
    """Validate a URL against an allowlist and return a DNS-pinned target.

    Raises UrlDenied on any SSRF indicator.
    """
    parsed = urlparse(url)
    scheme = (parsed.scheme or "").lower()

    if scheme not in {"http", "https"}:
        raise UrlDenied(f"Scheme {scheme!r} not allowed")
    if scheme == "http" and not policy.allow_http:
        raise UrlDenied("http:// requires explicit opt-in; use https://")

    raw_host = parsed.hostname
    if not raw_host:
        raise UrlDenied("URL has no hostname")
    host = _normalize_host(raw_host)

    if not policy.allow_hosts:
        raise UrlDenied("No hosts are allowlisted for this agent")
    if host not in policy.allow_hosts:
        raise UrlDenied(f"Host {host!r} is not in the allowlist")

    port = parsed.port or (443 if scheme == "https" else 80)
    if not (0 < port < 65536):
        raise UrlDenied(f"Invalid port {port}")

    ip = _resolve_and_validate(host)
    return ResolvedTarget(url=url, scheme=scheme, host=host, port=port, ip=ip)


def _resolve_and_validate(host: str) -> str:
    """Resolve host to a single IP and validate it against the blocklist.

    If the host resolves to multiple addresses, we pick the first non-blocked
    one. If all resolved addresses are blocked, we raise.
    """
    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:  # pragma: no cover — DNS-dependent
        raise UrlDenied(f"DNS resolution failed for {host!r}: {exc}") from exc

    errors: list[str] = []
    for family, _socktype, _proto, _canon, sockaddr in infos:
        ip_str = sockaddr[0]
        try:
            _check_ip(ip_str)
        except UrlDenied as exc:
            errors.append(str(exc))
            continue
        return ip_str

    raise UrlDenied(f"All resolved IPs for {host!r} are blocked: {errors}")


def _check_ip(ip_str: str) -> None:
    if ip_str in _BLOCKED_EXACT:
        raise UrlDenied(f"IP {ip_str} is on the exact-match block list")
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError as exc:
        raise UrlDenied(f"Invalid IP literal {ip_str!r}: {exc}") from exc

    if ip.is_loopback:
        raise UrlDenied(f"Loopback address {ip} blocked")
    if ip.is_private:
        raise UrlDenied(f"Private address {ip} blocked")
    if ip.is_link_local:
        raise UrlDenied(f"Link-local address {ip} blocked")
    if ip.is_multicast:
        raise UrlDenied(f"Multicast address {ip} blocked")
    if ip.is_reserved:
        raise UrlDenied(f"Reserved address {ip} blocked")
    if ip.is_unspecified:
        raise UrlDenied(f"Unspecified address {ip} blocked")

    # IPv4-mapped IPv6: unwrap and re-check so ::ffff:127.0.0.1 is blocked.
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        _check_ip(str(ip.ipv4_mapped))


@contextlib.contextmanager
def pin_dns(target: ResolvedTarget) -> Iterator[None]:
    """Force ``socket.getaddrinfo`` to return the pre-validated IP.

    The earlier "rebuild URL with the IP literal, set Host header" pattern
    breaks HTTPS: httpx verifies the cert against the URL hostname, which
    is now a bare IP, and every cert mismatches. Instead, leave the URL
    pointing at the original hostname (so SNI + cert verification work
    normally) and intercept the resolver inside this context so the TCP
    connection still goes to the IP we already validated against the
    private-IP / metadata block-list. No DNS rebinding window.

    Only resolutions of ``target.host`` are intercepted; other hosts fall
    through to the real resolver. Patch is process-global; callers should
    not run two pinned requests concurrently in the same process.
    """
    original = socket.getaddrinfo
    target_host = target.host.lower()

    def _patched(host, port, *args, **kwargs):  # type: ignore[no-untyped-def]
        if isinstance(host, str) and host.lower() == target_host:
            family = socket.AF_INET6 if ":" in target.ip else socket.AF_INET
            sockaddr = (target.ip, port or 0)
            if family == socket.AF_INET6:
                sockaddr = (target.ip, port or 0, 0, 0)
            return [(family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", sockaddr)]
        return original(host, port, *args, **kwargs)

    socket.getaddrinfo = _patched  # type: ignore[assignment]
    try:
        yield
    finally:
        socket.getaddrinfo = original  # type: ignore[assignment]
