"""Ops dashboard routes: health, data residency, plugin registry, YAML editor."""

from __future__ import annotations

import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from spark.config.loader import ConfigLoadError, load_agent, load_task
from spark.persistence.db import session_scope
from spark.persistence.models import PluginRegistryRow
from spark.sandbox.executor import SandboxUnavailable, check_available
from spark.web.auth import Principal, require_operator, require_viewer
from sqlalchemy import select

router = APIRouter()

MAX_YAML_BYTES = 256 * 1024  # 256 KiB per config file is plenty


class YamlPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    yaml: str = Field(min_length=1, max_length=MAX_YAML_BYTES)


@router.get("/health")
async def health() -> dict[str, object]:
    try:
        backend = check_available()
    except SandboxUnavailable as exc:
        return {"ok": False, "sandbox_error": str(exc)}
    return {"ok": True, "sandbox_backend": backend}


@router.get("/data-residency")
async def data_residency(_: Principal = Depends(require_viewer)) -> dict[str, object]:
    home = Path("~/.spark").expanduser()
    paths = {
        "db": home / "spark.db",
        "chroma": home / "chroma",
        "logs": home / "logs",
        "scheduler": home / "scheduler.db",
        "web_token": home / "web-token",
    }
    out: dict[str, object] = {}
    for key, p in paths.items():
        exists = p.exists()
        size = 0
        if exists:
            if p.is_dir():
                size = sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
            else:
                size = p.stat().st_size
        out[key] = {"path": str(p), "exists": exists, "size_bytes": size}
    total, used, free = shutil.disk_usage(home if home.exists() else home.parent)
    out["disk"] = {"total": total, "used": used, "free": free}
    return out


@router.get("/plugins")
async def list_plugins(_: Principal = Depends(require_viewer)) -> list[dict[str, object]]:
    async with session_scope() as session:
        result = await session.execute(select(PluginRegistryRow))
        rows = list(result.scalars().all())
    return [
        {
            "name": r.name,
            "version": r.version,
            "module_hash": r.module_hash,
            "first_seen_at": r.first_seen_at,
            "last_seen_at": r.last_seen_at,
        }
        for r in rows
    ]


@router.post("/validate/agent")
async def validate_agent_yaml(
    body: YamlPayload, _: Principal = Depends(require_operator)
) -> dict[str, object]:
    """Validate an agent YAML without persisting."""
    import tempfile

    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
        f.write(body.yaml)
        tmp = Path(f.name)
    try:
        agent = load_agent(tmp)
    except ConfigLoadError as exc:
        return {"ok": False, "errors": exc.errors}
    finally:
        tmp.unlink(missing_ok=True)
    return {
        "ok": True,
        "name": agent.metadata.name,
        "provider": agent.spec.runtime.provider.type,
        "plugins": agent.spec.plugins.allow,
    }


@router.post("/validate/task")
async def validate_task_yaml(
    body: YamlPayload, _: Principal = Depends(require_operator)
) -> dict[str, object]:
    import tempfile

    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
        f.write(body.yaml)
        tmp = Path(f.name)
    try:
        task = load_task(tmp)
    except ConfigLoadError as exc:
        return {"ok": False, "errors": exc.errors}
    finally:
        tmp.unlink(missing_ok=True)
    return {"ok": True, "name": task.metadata.name, "mode": task.spec.mode.value}
