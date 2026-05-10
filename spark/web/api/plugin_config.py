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


# ---------------------------------------------------------------------------
# Plugin-specific discovery endpoints.
# Plugins that ship a custom Plugins-page editor can add a discover()
# coroutine + a route here. For v1 only home_assistant has one; if a
# second plugin needs the same, generalize to a `/discover` dispatcher
# keyed on a registry of discover handlers.
# ---------------------------------------------------------------------------


@router.post("/home_assistant/discover")
async def home_assistant_discover(
    principal: Principal = Depends(require_operator),
) -> dict[str, Any]:
    """Read-only HA introspection used by the live-config editor.

    Loads the saved ``home_assistant`` config + resolves the configured
    secret, then calls ``home_assistant.discover()``. The plugin's
    `discover` returns a ``HomeAssistantDiscovery`` with ``ok=false`` and
    a ``error_code`` matching the SparkError code on any failure path,
    so the editor can render the same FailureInspector compact panel
    that runtime errors do.
    """
    from spark.plugins.builtins.home_assistant import (  # noqa: PLC0415
        HomeAssistantPlugin,
        discover as _discover,
    )

    handle = _plugin_or_404("home_assistant")
    loaded = await load_plugin_config(
        "home_assistant", handle.cls.config_schema
    )
    cfg = dict(loaded.defaults)

    # Build a minimal context with just `secrets` populated — enough
    # for the plugin's `_resolve_token`. Avoids spinning up the full
    # ToolContext shape since discover is a pure read-only call.
    from spark.runtime import get_secret_manager  # noqa: PLC0415

    secrets: dict[str, str] = {}
    try:
        mgr = get_secret_manager()
        secret_name = cfg.get("token_secret") or "home_assistant_token"
        try:
            value = mgr.get(secret_name)
            secrets[secret_name] = value.get_secret_value()
        except Exception:
            # Leave secrets empty; plugin returns SECRET_NOT_FOUND via
            # discover()'s own error path.
            pass
    except Exception:  # pragma: no cover — boot path
        pass

    class _Ctx:
        pass

    ctx = _Ctx()
    ctx.secrets = secrets  # type: ignore[attr-defined]

    result = await _discover(cfg, ctx)
    payload = result.model_dump()
    # Audit at info — discovery is read-only and may be hit on every
    # editor open. Same severity precedent as the filtering dry-run.
    try:
        from spark.persistence.db import session_scope  # noqa: PLC0415
        from spark.persistence.learning_repos import (  # noqa: PLC0415
            AuditRepository,
        )

        async with session_scope() as session:
            await AuditRepository(session).append(
                actor=principal.subject,
                kind="security.plugin.discover",
                target="home_assistant",
                diff={
                    "ok": payload.get("ok"),
                    "error_code": payload.get("error_code"),
                    "domain_count": len(payload.get("domains", []) or []),
                    "entity_count": len(payload.get("entities", []) or []),
                },
                severity="info",
            )
    except Exception:  # pragma: no cover — never escalate audit failures
        pass
    # Touch the unused class-import so linters don't warn (kept for
    # future generalization to a discover dispatcher keyed on plugin).
    _ = HomeAssistantPlugin
    return payload
