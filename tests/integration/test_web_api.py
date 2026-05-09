"""Integration tests for the FastAPI web layer.

Uses a `SparkRuntime` config with `web.enabled=true` and `bind.mode=loopback`.
The token fallback path is exercised via the `x-spark-token` header.
"""

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
    token = ensure_token(rotate=True)

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
    app, fresh = build_app_with_auth(cfg, rotate_credentials=True, rotate_token=False)
    assert fresh is not None
    with TestClient(app) as c:
        yield c, token, fresh
    await dispose()


def test_health(client):
    c, _, _ = client
    resp = c.get("/api/health")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


def test_login_with_password(client):
    c, _, creds = client
    resp = c.post(
        "/api/auth/login",
        json={"username": creds.username, "password": creds.password},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["subject"] == creds.username
    assert body["role"] == "operator"
    me = c.get("/api/auth/me")
    assert me.status_code == 200


def test_login_bad_password(client):
    c, _, creds = client
    resp = c.post(
        "/api/auth/login",
        json={"username": creds.username, "password": "wrong-password"},
    )
    assert resp.status_code == 401


def test_login_bad_username(client):
    c, _, creds = client
    resp = c.post(
        "/api/auth/login",
        json={"username": "not-" + creds.username, "password": creds.password},
    )
    assert resp.status_code == 401


def test_unauth_request_rejected(client):
    c, _, _ = client
    resp = c.get("/api/scheduler/agents")
    assert resp.status_code == 401


def test_token_header_short_circuits(client):
    c, token, _ = client
    resp = c.get("/api/security/global", headers={"x-spark-token": token})
    assert resp.status_code == 200


def test_posture_defaults(client):
    c, token, _ = client
    data = c.get("/api/security/global", headers={"x-spark-token": token}).json()
    assert data["frozen"] is False
    assert data["compliance_mode"] == "standard"


def test_internal_grant_requires_confirmation(client):
    c, token, _ = client
    resp = c.post(
        "/api/security/internal-grants",
        json={
            "agent_name": "alpha",
            "cidr": "10.0.0.0/24",
            "reason": "testing",
            "ttl_hours": 1,
            "confirm_agent_name": "wrong",
        },
        headers={"x-spark-token": token},
    )
    assert resp.status_code == 400


def test_internal_grant_happy_path(client):
    c, token, _ = client
    resp = c.post(
        "/api/security/internal-grants",
        json={
            "agent_name": "alpha",
            "cidr": "10.0.5.0/24",
            "reason": "k8s api",
            "ttl_hours": 2,
            "confirm_agent_name": "alpha",
        },
        headers={"x-spark-token": token},
    )
    assert resp.status_code == 200
    listing = c.get(
        "/api/security/internal-grants/alpha", headers={"x-spark-token": token}
    ).json()
    assert any(g["cidr"] == "10.0.5.0/24" for g in listing)


def test_trusted_doc_add_remove(client):
    c, token, _ = client
    resp = c.post(
        "/api/security/trusted-docs",
        json={"host": "docs.custom.example", "notes": "test"},
        headers={"x-spark-token": token},
    )
    assert resp.status_code == 200
    listing = c.get("/api/security/trusted-docs", headers={"x-spark-token": token}).json()
    assert any(d["host"] == "docs.custom.example" for d in listing)
    resp = c.delete(
        "/api/security/trusted-docs/docs.custom.example",
        headers={"x-spark-token": token},
    )
    assert resp.status_code == 200


def test_freeze_unfreeze_cycle(client):
    c, token, _ = client
    r = c.post(
        "/api/security/global/freeze?reason=test",
        headers={"x-spark-token": token},
    )
    assert r.status_code == 200
    posture = c.get("/api/security/global", headers={"x-spark-token": token}).json()
    assert posture["frozen"] is True
    r = c.post("/api/security/global/unfreeze", headers={"x-spark-token": token})
    assert r.status_code == 200
    posture = c.get("/api/security/global", headers={"x-spark-token": token}).json()
    assert posture["frozen"] is False
