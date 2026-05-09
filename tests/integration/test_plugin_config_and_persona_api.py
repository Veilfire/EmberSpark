"""Integration tests for the plugin-config and persona API surfaces."""

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


# --------------------------------------------------------------------------
# Plugin config endpoints
# --------------------------------------------------------------------------


def test_list_plugins_includes_builtins(client):
    c, creds = client
    c.post("/api/auth/login", json={"username": creds.username, "password": creds.password})
    resp = c.get("/api/plugin-config/")
    assert resp.status_code == 200
    names = {p["plugin_name"] for p in resp.json()}
    # F1 builtins + F3 new plugins
    assert {"filesystem", "http_client", "markdown_writer", "shell", "sqlite"}.issubset(names)


def test_update_http_client_narrows_allow_hosts(client):
    c, creds = client
    c.post("/api/auth/login", json={"username": creds.username, "password": creds.password})
    resp = c.put(
        "/api/plugin-config/http_client",
        json={
            "config": {
                "allow_hosts": ["api.github.com"],
                "allow_http": False,
                "allowed_methods": ["GET"],
                "max_response_bytes": 5_000_000,
                "connect_timeout_seconds": 5.0,
                "read_timeout_seconds": 15.0,
                "user_agent": "test-agent/1.0",
            },
            "reason": "narrowing for test",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["config"]["allow_hosts"] == ["api.github.com"]
    assert data["config"]["user_agent"] == "test-agent/1.0"


def test_update_plugin_config_rejects_unknown_plugin(client):
    c, creds = client
    c.post("/api/auth/login", json={"username": creds.username, "password": creds.password})
    resp = c.put("/api/plugin-config/totally-fake", json={"config": {}, "reason": ""})
    assert resp.status_code == 404


def test_plugin_config_audit_entry_written(client):
    c, creds = client
    c.post("/api/auth/login", json={"username": creds.username, "password": creds.password})
    c.put(
        "/api/plugin-config/markdown_writer",
        json={
            "config": {
                "allow_paths": ["/tmp/spark-test"],
                "deny_paths": [],
                "allow_append": True,
                "allow_overwrite": False,
            },
            "reason": "",
        },
    )
    audit = c.get("/api/audit/?kind=plugin.config.update").json()
    assert any(e["target"] == "markdown_writer" for e in audit)


# --------------------------------------------------------------------------
# Persona endpoints
# --------------------------------------------------------------------------


def test_persona_create_update_activate_flow(client):
    c, creds = client
    c.post("/api/auth/login", json={"username": creds.username, "password": creds.password})

    # Seeded persona exists on startup.
    listing = c.get("/api/persona/").json()
    assert len(listing) >= 1

    # Create a new persona.
    created = c.post(
        "/api/persona/",
        json={
            "name": "Concise",
            "description": "Short, direct, no fluff.",
            "system_prompt": "Respond tersely.",
            "tone": "terse",
            "tags": ["test"],
        },
    ).json()
    persona_id = created["persona_id"]
    assert created["is_active"] is False

    # Activate it.
    activated = c.post(
        f"/api/persona/{persona_id}/activate",
    ).json()
    assert activated["is_active"] is True
    # The previously-active persona must now be inactive.
    listing2 = c.get("/api/persona/").json()
    active_count = sum(1 for p in listing2 if p["is_active"])
    assert active_count == 1

    # Delete of the active persona must be refused (409).
    delete_resp = c.delete(f"/api/persona/{persona_id}")
    assert delete_resp.status_code == 409


def test_persona_preview_returns_assembled_prompt(client):
    c, creds = client
    c.post("/api/auth/login", json={"username": creds.username, "password": creds.password})
    listing = c.get("/api/persona/").json()
    persona_id = listing[0]["persona_id"]
    resp = c.post(f"/api/persona/{persona_id}/preview", json={"objective": "say hi"})
    assert resp.status_code == 200
    data = resp.json()
    assert "system_prompt" in data
    assert data["char_count"] > 0
