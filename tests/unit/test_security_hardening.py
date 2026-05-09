"""Unit tests for the security hardening fixes.

Each test maps to one finding from the security review.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from spark.privacy.redaction import disable_presidio, redact
from spark.utils.net import HostPolicy, UrlDenied, _normalize_host, validate_url

disable_presidio()


# ---- IDN homoglyph: fail closed ----------------------------------------------


def test_idna_non_ascii_rejected_after_normalization() -> None:
    # Pure-ASCII hosts normalize to themselves.
    assert _normalize_host("api.github.com") == "api.github.com"
    # Cyrillic "а" in "аpple.com" is encoded to punycode, which cannot equal
    # the ASCII allowlist entry "apple.com".
    normalized = _normalize_host("\u0430pple.com")
    assert normalized != "apple.com"
    assert normalized.startswith("xn--")


def test_homoglyph_blocked_against_allowlist() -> None:
    policy = HostPolicy.from_list(["apple.com"])
    with patch(
        "spark.utils.net.socket.getaddrinfo",
        return_value=[(None, None, None, None, ("93.184.216.34", 0))],
    ):
        with pytest.raises(UrlDenied):
            validate_url("https://\u0430pple.com/", policy)


# ---- Entropy threshold lowered -----------------------------------------------


def test_short_high_entropy_token_now_scrubbed() -> None:
    # 16 chars — would have been missed under the previous 24-char rule.
    short = "Zk8HqR2vMNp6aXgC"
    result = redact(f"token={short}", use_presidio=False)
    assert short not in result.text
    assert "HIGH_ENTROPY" in result.applied


def test_ordinary_english_not_scrubbed() -> None:
    result = redact("the quick brown fox jumps over the lazy dog", use_presidio=False)
    assert result.applied == ()


# ---- bcrypt rounds bumped ----------------------------------------------------


def test_bcrypt_uses_at_least_13_rounds() -> None:
    from spark.web.credentials import hash_password

    h = hash_password("some-password-1!Aa")
    # bcrypt modular crypt format: $2b$13$<salt+hash>
    assert h.startswith("$2b$13$") or h.startswith("$2a$13$")


# ---- CidrAllowlistMiddleware: XFF parsing ------------------------------------


def test_xff_leftmost_malformed_is_ignored() -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from spark.web.middleware import CidrAllowlistMiddleware

    app = FastAPI()
    app.add_middleware(
        CidrAllowlistMiddleware,
        allowed_cidrs=["192.168.1.0/24"],
        trusted_proxies=["127.0.0.1"],
    )

    @app.get("/api/data")
    async def data():
        return {"ok": True}

    with TestClient(app) as c:
        # Malformed XFF must fall back to the raw peer (127.0.0.1) which is
        # not in the allowlist → 403.
        resp = c.get("/api/data", headers={"X-Forwarded-For": "not-an-ip"})
    assert resp.status_code == 403


def test_xff_bracketed_ipv6_allowed_when_trusted() -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from spark.web.middleware import CidrAllowlistMiddleware

    app = FastAPI()
    app.add_middleware(
        CidrAllowlistMiddleware,
        allowed_cidrs=["fd00::/8"],
        trusted_proxies=["127.0.0.1"],
    )

    @app.get("/api/data")
    async def data():
        return {"ok": True}

    with TestClient(app) as c:
        resp = c.get("/api/data", headers={"X-Forwarded-For": "[fd00::1]"})
    assert resp.status_code == 200


# ---- SecurityHeadersMiddleware -----------------------------------------------


def test_security_headers_set_on_every_response() -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from spark.web.middleware import SecurityHeadersMiddleware

    app = FastAPI()
    app.add_middleware(SecurityHeadersMiddleware)

    @app.get("/")
    async def root():
        return {"ok": True}

    with TestClient(app) as c:
        resp = c.get("/")
    assert resp.status_code == 200
    h = resp.headers
    assert h["x-content-type-options"] == "nosniff"
    assert h["x-frame-options"] == "DENY"
    assert h["referrer-policy"] == "no-referrer"
    assert "default-src 'self'" in h["content-security-policy"]
    assert "frame-ancestors 'none'" in h["content-security-policy"]
    # HSTS is NOT set for http scheme (test client uses http)
    assert "strict-transport-security" not in h


def test_hsts_set_when_forwarded_proto_https() -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from spark.web.middleware import SecurityHeadersMiddleware

    app = FastAPI()
    app.add_middleware(SecurityHeadersMiddleware)

    @app.get("/")
    async def root():
        return {"ok": True}

    with TestClient(app) as c:
        resp = c.get("/", headers={"X-Forwarded-Proto": "https"})
    assert "strict-transport-security" in resp.headers


# ---- Filesystem TOCTOU: no mkdir from plugin ---------------------------------


@pytest.mark.asyncio
async def test_filesystem_plugin_refuses_write_to_symlinked_parent(tmp_path: Path) -> None:
    from spark.plugins.builtins.filesystem import FilesystemArgs, FilesystemPlugin

    allowed = tmp_path / "allowed"
    allowed.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    # Replace `allowed/inner` with a symlink to `outside`.
    inner = allowed / "inner"
    os.symlink(outside, inner)

    plugin = FilesystemPlugin()
    args = FilesystemArgs(
        op="write",
        path=str(inner / "note.txt"),
        content="hello",
        allow_paths=[str(allowed)],
    )
    # The PathPolicy check runs first and catches the symlink escape.
    # If it didn't, the write code path also raises because parent is a symlink.
    with pytest.raises((PermissionError, Exception)):
        await plugin.execute(args, None)


@pytest.mark.asyncio
async def test_filesystem_plugin_refuses_write_when_parent_missing(tmp_path: Path) -> None:
    from spark.plugins.builtins.filesystem import FilesystemArgs, FilesystemPlugin

    allowed = tmp_path / "allowed"
    allowed.mkdir()
    plugin = FilesystemPlugin()
    args = FilesystemArgs(
        op="write",
        path=str(allowed / "new" / "sub" / "file.txt"),
        content="hello",
        allow_paths=[str(allowed)],
    )
    with pytest.raises(PermissionError):
        await plugin.execute(args, None)


# ---- ValidationError sanitization --------------------------------------------


@pytest.mark.asyncio
async def test_tool_runtime_redacts_validation_error_details() -> None:
    from typing import Any, ClassVar
    from unittest.mock import AsyncMock

    from pydantic import BaseModel

    from spark.config.enums import Permission, Sensitivity
    from spark.config.loader import load_agent
    from spark.plugins.base import PermissionDenied
    from spark.plugins.registry import PluginRegistry
    from spark.plugins.tool_runtime import BudgetGuard, ToolExecutor
    from spark.secrets import SecretManager
    from spark.secrets.env_backend import EnvBackend

    class _Args(BaseModel):
        api_key: str
        other: str

    class _Out(BaseModel):
        ok: bool

    class _Plug:
        name: ClassVar[str] = "plug"
        version: ClassVar[str] = "0.1.0"
        description: ClassVar[str] = ""
        input_schema: ClassVar[type[BaseModel]] = _Args
        output_schema: ClassVar[type[BaseModel]] = _Out
        required_permissions: ClassVar[frozenset[Permission]] = frozenset()
        required_secrets: ClassVar[frozenset[str]] = frozenset()
        sensitivity: ClassVar[Sensitivity] = Sensitivity.LOW
        filter_output_before_model: ClassVar[bool] = True
        needs_network: ClassVar[bool] = False

        async def execute(self, args: _Args, ctx: Any) -> _Out:  # pragma: no cover
            return _Out(ok=True)

    yaml_path = Path(__file__).parent / "_agent_sanitize.yaml"
    yaml_path.write_text(
        """
apiVersion: spark.veilfire.dev/v1alpha1
kind: Agent
metadata:
  name: sanitize-test
spec:
  description: t
  runtime:
    provider:
      type: openai
      model: gpt-4.1
      api_key_ref: openai_key
    max_iterations: 2
    max_model_calls: 2
    max_tool_calls: 2
  plugins:
    allow: [plug]
""".strip()
    )
    try:
        agent = load_agent(yaml_path)
    finally:
        yaml_path.unlink(missing_ok=True)

    reg = PluginRegistry()
    reg.register_class(_Plug)
    executor = ToolExecutor(
        registry=reg,
        secrets=SecretManager([EnvBackend(silence_warning=True)]),
        agent_spec=agent.spec,
        budget=BudgetGuard(max_tool_calls=5, max_model_calls=5, max_iterations=5),
    )

    try:
        await executor.call("plug", {"wrong_field": "x"})
    except PermissionDenied as exc:
        # The error message must NOT echo the schema field names.
        assert "api_key" not in str(exc)
        assert "other" not in str(exc)
        assert "validation error" in str(exc)
        return
    pytest.fail("expected PermissionDenied")
