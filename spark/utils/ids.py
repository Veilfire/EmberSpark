"""Stable identifier generation."""

from __future__ import annotations

import secrets
import uuid
from datetime import datetime, timezone


def new_uuid() -> str:
    return str(uuid.uuid4())


def new_task_run_id() -> str:
    ts = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%S")
    return f"run-{ts}-{secrets.token_hex(4)}"


def new_memory_id(prefix: str = "mem") -> str:
    return f"{prefix}-{secrets.token_hex(8)}"


def short_id(n: int = 6) -> str:
    return secrets.token_hex(n // 2 + 1)[:n]
