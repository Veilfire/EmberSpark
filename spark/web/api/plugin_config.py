"""Plugin configuration routes.

All config mutations are audited at ``elevated`` severity because plugin
config directly controls what the agent can touch (hosts, paths, methods).
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from spark.plugins.config import (
    LoadedPluginConfig,
    load_plugin_config,
    reset_plugin_config,
    save_plugin_config,
    schema_hash,
)
from spark.plugins.registry import default_registry
from spark.web.auth import Principal, require_operator, require_viewer

router = APIRouter()

_registry = default_registry()


def _plugin_or_404(plugin_name: str):  # type: ignore[no-untyped-def]
    try:
        return _registry.get(plugin_name)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="plugin not found") from exc


class PluginConfigResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plugin_name: str
    version: str
    description: str
    config: dict[str, Any]
    schema: dict[str, Any]
    schema_hash: str
    fresh: bool


class PluginConfigUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    config: dict[str, Any] = Field(default_factory=dict)
    reason: str = Field(default="", max_length=1000)


async def _render(plugin_name: str, loaded: LoadedPluginConfig) -> PluginConfigResponse:
    handle = _registry.get(plugin_name)
    return PluginConfigResponse(
        plugin_name=plugin_name,
        version=handle.cls.version,
        description=handle.cls.description,
        config=loaded.defaults,
        schema=handle.cls.config_schema.model_json_schema(),
        schema_hash=loaded.schema_hash,
        fresh=loaded.fresh,
    )


@router.get("/")
async def list_plugins(
    _: Principal = Depends(require_viewer),
) -> list[PluginConfigResponse]:
    out: list[PluginConfigResponse] = []
    for name in _registry.names():
        handle = _registry.get(name)
        loaded = await load_plugin_config(name, handle.cls.config_schema)
        out.append(await _render(name, loaded))
    return out


@router.get("/{plugin_name}", response_model=PluginConfigResponse)
async def get_plugin_config(
    plugin_name: str, _: Principal = Depends(require_viewer)
) -> PluginConfigResponse:
    handle = _plugin_or_404(plugin_name)
    loaded = await load_plugin_config(plugin_name, handle.cls.config_schema)
    return await _render(plugin_name, loaded)


@router.put("/{plugin_name}", response_model=PluginConfigResponse)
async def update_plugin_config(
    plugin_name: str,
    body: PluginConfigUpdate,
    principal: Principal = Depends(require_operator),
) -> PluginConfigResponse:
    handle = _plugin_or_404(plugin_name)
    try:
        loaded = await save_plugin_config(
            plugin_name=plugin_name,
            config_schema=handle.cls.config_schema,
            raw=body.config,
            updated_by=principal.subject,
            reason=body.reason,
        )
    except ValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail={"errors": json.loads(exc.json())},
        ) from exc
    return await _render(plugin_name, loaded)


@router.post("/{plugin_name}/reset")
async def reset(
    plugin_name: str,
    principal: Principal = Depends(require_operator),
) -> dict[str, bool]:
    _plugin_or_404(plugin_name)
    removed = await reset_plugin_config(plugin_name, updated_by=principal.subject)
    return {"ok": removed}
