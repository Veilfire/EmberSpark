"""Spark web UI — FastAPI backend + React frontend.

The web surface is explicitly a privileged operator console. By default it
binds to 127.0.0.1. Non-loopback binds require a token; operators are warned
prominently on startup.

Modules:
- `app.py`           — FastAPI factory
- `auth.py`          — token + session + role gates
- `deps.py`          — DI helpers for route handlers
- `schemas.py`       — request/response Pydantic models
- `events.py`        — in-process event bus for SSE + WebSocket streams
- `api/*`            — route modules split by domain
"""

from __future__ import annotations

from spark.web.app import build_app

__all__ = ["build_app"]
