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


# ---- chat session rename / pin / delete --------------------------------------


async def _seed_session(
    session_id: str,
    name: str,
    *,
    agent_name: str = "agent-x",
    title: str | None = None,
    pinned: bool = False,
) -> None:
    from spark.persistence.db import session_scope
    from spark.persistence.models import SessionRow

    async with session_scope() as session:
        session.add(
            SessionRow(
                session_id=session_id,
                name=name,
                agent_name=agent_name,
                title=title,
                pinned=pinned,
            )
        )


async def test_chat_rename_and_pin_ordering(client):
    c, token, _ = client
    h = {"x-spark-token": token}
    await _seed_session("chat-aaa", "one")
    await _seed_session("chat-bbb", "two")

    # Rename writes the free-text title.
    r = c.put("/api/chat/sessions/chat-aaa", json={"title": "my renamed chat"}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["title"] == "my renamed chat"
    assert r.json()["pinned"] is False

    # Pinning floats a chat to the top of the list.
    r = c.put("/api/chat/sessions/chat-bbb", json={"pinned": True}, headers=h)
    assert r.status_code == 200
    assert r.json()["pinned"] is True

    listing = c.get("/api/chat/sessions", headers=h).json()
    ids = [s["session_id"] for s in listing]
    assert ids.index("chat-bbb") < ids.index("chat-aaa")
    assert "pinned" in listing[0] and "title" in listing[0]

    # Empty update -> 400; unknown session -> 404; malformed id -> 400.
    assert c.put("/api/chat/sessions/chat-aaa", json={}, headers=h).status_code == 400
    assert (
        c.put("/api/chat/sessions/chat-zzz", json={"title": "x"}, headers=h).status_code
        == 404
    )
    assert (
        c.put("/api/chat/sessions/bad id!", json={"title": "x"}, headers=h).status_code
        == 400
    )


async def test_chat_delete_cascades(client):
    c, token, _ = client
    h = {"x-spark-token": token}
    from sqlalchemy import func, select

    from spark.persistence.db import session_scope
    from spark.persistence.models import ChatTurnRow, SessionMemoryRow

    await _seed_session("chat-del", "to-delete")
    async with session_scope() as session:
        session.add(SessionMemoryRow(session_id="chat-del", kind="user", content="hi"))
        session.add(
            ChatTurnRow(
                turn_id="turn-del-1",
                session_id="chat-del",
                agent_name="agent-x",
                state="completed",
                user_message="hi",
            )
        )

    r = c.delete("/api/chat/sessions/chat-del", headers=h)
    assert r.status_code == 200 and r.json() == {"ok": True}

    ids = [s["session_id"] for s in c.get("/api/chat/sessions", headers=h).json()]
    assert "chat-del" not in ids

    # Dependent rows are cascade-cleaned (no DB-level FK does it for us).
    async with session_scope() as session:
        mem = (
            await session.execute(
                select(func.count())
                .select_from(SessionMemoryRow)
                .where(SessionMemoryRow.session_id == "chat-del")
            )
        ).scalar_one()
        turns = (
            await session.execute(
                select(func.count())
                .select_from(ChatTurnRow)
                .where(ChatTurnRow.session_id == "chat-del")
            )
        ).scalar_one()
    assert mem == 0
    assert turns == 0

    # Deleting again -> 404.
    assert c.delete("/api/chat/sessions/chat-del", headers=h).status_code == 404


def test_chat_session_mutations_require_auth(client):
    c, _, _ = client
    assert c.put("/api/chat/sessions/chat-x", json={"title": "x"}).status_code == 401
    assert c.delete("/api/chat/sessions/chat-x").status_code == 401
