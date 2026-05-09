"""Tests for CidrAllowlistMiddleware."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from spark.web.middleware import CidrAllowlistMiddleware


def _app(allowed: list[str], trusted_proxies: list[str] | None = None) -> FastAPI:
    app = FastAPI()
    app.add_middleware(
        CidrAllowlistMiddleware,
        allowed_cidrs=allowed,
        trusted_proxies=trusted_proxies or [],
    )

    @app.get("/api/health")
    async def health():
        return {"ok": True}

    @app.get("/api/data")
    async def data():
        return {"data": 42}

    return app


def test_health_always_allowed() -> None:
    app = _app(allowed=["10.0.0.0/24"])
    with TestClient(app) as c:
        resp = c.get("/api/health")
    assert resp.status_code == 200


def test_loopback_client_blocked_when_not_in_cidr() -> None:
    app = _app(allowed=["10.0.0.0/24"])
    with TestClient(app) as c:
        resp = c.get("/api/data")
    # TestClient reports 127.0.0.1 as client; not in 10.0.0.0/24 → 403
    assert resp.status_code == 403


def test_loopback_client_allowed_when_in_cidr() -> None:
    app = _app(allowed=["127.0.0.0/8"])
    with TestClient(app) as c:
        resp = c.get("/api/data")
    assert resp.status_code == 200


def test_trusted_proxy_honors_xff() -> None:
    # TestClient sends from 127.0.0.1; we mark it trusted and send an XFF
    # header pointing at an allowed LAN IP.
    app = _app(allowed=["192.168.1.0/24"], trusted_proxies=["127.0.0.1"])
    with TestClient(app) as c:
        resp = c.get("/api/data", headers={"X-Forwarded-For": "192.168.1.42"})
    assert resp.status_code == 200


def test_untrusted_proxy_ignores_xff() -> None:
    app = _app(allowed=["192.168.1.0/24"], trusted_proxies=[])  # no trusted proxies
    with TestClient(app) as c:
        resp = c.get("/api/data", headers={"X-Forwarded-For": "192.168.1.42"})
    assert resp.status_code == 403
