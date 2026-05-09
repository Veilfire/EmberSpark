"""Integration tests for web-layer security hardening."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from spark.config.runtime_config import (
    SparkRuntime,
    SparkRuntimeSpec,
    WebBindLoopback,
    WebConfig,
    WebCredentialsConfig,
)
from spark.persistence.db import dispose, init_db
from spark.web.app import build_app_with_auth
from spark.web.auth import ensure_token


@pytest.fixture
async def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".spark").mkdir()
    ensure_token(rotate=True)
    await init_db(tmp_path / ".spark/spark.db")
    cfg = SparkRuntime(
        spec=SparkRuntimeSpec(
            web=WebConfig(
                enabled=True,
                bind=WebBindLoopback(host="127.0.0.1", port=7777),
                credentials=WebCredentialsConfig(
                    rotate_on_startup=True,
                    path=tmp_path / ".spark/web-credentials.json",
                ),
                session_ttl_seconds=3600,
                rate_limit_per_minute=0,
            )
        )
    )
    app, fresh = build_app_with_auth(cfg, rotate_credentials=True)
    with TestClient(app) as c:
        yield c, fresh
    await dispose()


# ---- Unvalidated bodies ------------------------------------------------------


def test_create_session_rejects_dict_with_extra_fields(client):
    c, creds = client
    c.post("/api/auth/login", json={"username": creds.username, "password": creds.password})
    resp = c.post(
        "/api/chat/sessions",
        json={"agent_name": "x", "hidden_field": "boom"},
    )
    assert resp.status_code == 422


def test_create_session_rejects_bad_agent_name(client):
    c, creds = client
    c.post("/api/auth/login", json={"username": creds.username, "password": creds.password})
    resp = c.post(
        "/api/chat/sessions",
        json={"agent_name": "../../etc/passwd"},
    )
    assert resp.status_code == 422


def test_validate_agent_rejects_oversized_yaml(client):
    c, creds = client
    c.post("/api/auth/login", json={"username": creds.username, "password": creds.password})
    big = "a: " + ("x" * (512 * 1024))
    resp = c.post("/api/ops/validate/agent", json={"yaml": big})
    assert resp.status_code == 422


def test_validate_agent_rejects_extra_fields(client):
    c, creds = client
    c.post("/api/auth/login", json={"username": creds.username, "password": creds.password})
    resp = c.post(
        "/api/ops/validate/agent",
        json={"yaml": "hi", "extra": "nope"},
    )
    assert resp.status_code == 422


def test_secret_canary_validates_name(client):
    c, creds = client
    c.post("/api/auth/login", json={"username": creds.username, "password": creds.password})
    # Name with path traversal — schema rejects.
    resp = c.post("/api/security/secrets/canary", json={"name": "../../etc/passwd"})
    assert resp.status_code == 422


def test_secret_canary_writes_audit_entry(client):
    c, creds = client
    c.post("/api/auth/login", json={"username": creds.username, "password": creds.password})
    resp = c.post("/api/security/secrets/canary", json={"name": "nonexistent-key"})
    assert resp.status_code == 200
    # Audit entry for the probe must exist.
    audit = c.get("/api/audit/?kind=security.secret.canary").json()
    assert len(audit) >= 1
    assert audit[0]["target"] == "nonexistent-key"


# ---- CORS wildcard refusal ---------------------------------------------------


def test_cors_wildcard_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("SPARK_WEB_ALLOW_ORIGIN", "*")
    (tmp_path / ".spark").mkdir()
    cfg = SparkRuntime(
        spec=SparkRuntimeSpec(
            web=WebConfig(
                enabled=True,
                bind=WebBindLoopback(),
                credentials=WebCredentialsConfig(
                    path=tmp_path / ".spark/web-credentials.json"
                ),
                rate_limit_per_minute=0,
            )
        )
    )
    with pytest.raises(RuntimeError, match="wildcard"):
        build_app_with_auth(cfg, rotate_credentials=True)


# ---- Security headers ON every response --------------------------------------


def test_security_headers_present(client):
    c, _ = client
    resp = c.get("/api/health")
    assert resp.status_code == 200
    assert resp.headers["x-frame-options"] == "DENY"
    assert "default-src 'self'" in resp.headers["content-security-policy"]
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert "camera=()" in resp.headers["permissions-policy"]
