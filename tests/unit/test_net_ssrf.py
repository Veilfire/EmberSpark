"""Tests for spark.utils.net — SSRF / DNS-rebinding defenses."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from spark.utils.net import HostPolicy, UrlDenied, validate_url


def _fake_resolve(ip: str):
    return [(None, None, None, None, (ip, 0))]


def test_scheme_must_be_https_by_default() -> None:
    policy = HostPolicy.from_list(["example.com"])
    with pytest.raises(UrlDenied, match="http://"):
        validate_url("http://example.com/", policy)


def test_allow_http_opt_in() -> None:
    policy = HostPolicy.from_list(["example.com"], allow_http=True)
    with patch("spark.utils.net.socket.getaddrinfo", return_value=_fake_resolve("93.184.216.34")):
        target = validate_url("http://example.com/", policy)
    assert target.scheme == "http"
    assert target.ip == "93.184.216.34"


def test_host_not_in_allowlist_rejected() -> None:
    policy = HostPolicy.from_list(["example.com"])
    with pytest.raises(UrlDenied, match="not in the allowlist"):
        validate_url("https://evil.com/", policy)


@pytest.mark.parametrize(
    "ip",
    [
        "127.0.0.1",
        "10.0.0.5",
        "172.16.5.9",
        "192.168.1.1",
        "169.254.169.254",   # AWS IMDS
        "0.0.0.0",
        "::1",
        "fe80::1",
        "fc00::1",
        "fd00:ec2::254",
    ],
)
def test_blocked_ips_rejected(ip: str) -> None:
    policy = HostPolicy.from_list(["evil-cdn.example"])
    with patch("spark.utils.net.socket.getaddrinfo", return_value=_fake_resolve(ip)):
        with pytest.raises(UrlDenied):
            validate_url("https://evil-cdn.example/", policy)


def test_ipv4_mapped_ipv6_loopback_rejected() -> None:
    policy = HostPolicy.from_list(["rebind.example"])
    with patch(
        "spark.utils.net.socket.getaddrinfo",
        return_value=_fake_resolve("::ffff:127.0.0.1"),
    ):
        with pytest.raises(UrlDenied):
            validate_url("https://rebind.example/", policy)


def test_idn_homoglyph_normalized() -> None:
    # The "a" is Cyrillic; it must not match "apple.com" unless decoded.
    policy = HostPolicy.from_list(["apple.com"])
    with pytest.raises(UrlDenied):
        validate_url("https://\u0430pple.com/", policy)


def test_public_ip_passes_through() -> None:
    policy = HostPolicy.from_list(["api.github.com"])
    with patch(
        "spark.utils.net.socket.getaddrinfo",
        return_value=_fake_resolve("140.82.114.6"),
    ):
        target = validate_url("https://api.github.com/repos/foo", policy)
    assert target.host == "api.github.com"
    assert target.ip == "140.82.114.6"
    assert target.port == 443


def test_empty_allowlist_fails_closed() -> None:
    policy = HostPolicy.from_list([])
    with pytest.raises(UrlDenied, match="No hosts are allowlisted"):
        validate_url("https://example.com/", policy)
