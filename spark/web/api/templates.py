"""Agent template routes (H1.1).

Lets an operator browse, preview, and install one of the ready-to-run
templates shipped under ``examples/templates/``.
"""

from __future__ import annotations

import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from spark.persistence.db import session_scope
from spark.persistence.learning_repos import AuditRepository
from spark.templates import TemplateNotFound, list_templates, load_template
from spark.templates.loader import templates_root, _invalidate_cache
from spark.web.auth import Principal, require_admin, require_operator, require_viewer

router = APIRouter()


# ---------------------------------------------------------------------------
# View models
# ---------------------------------------------------------------------------


class TemplateSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    description: str
    plugins_required: list[str]
    permissions_required: list[str]
    secrets_required: list[str]


class TemplateDetail(TemplateSummary):
    model_config = ConfigDict(extra="forbid")
    readme: str
    agent_yaml: str
    task_yaml: str
    plugin_config_hints: dict[str, Any]


class InstallRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_dir: Path | None = Field(
        default=None,
        description=(
            "Override for the install destination. Defaults to ``~/.spark/`` "
            "which produces ``~/.spark/agents/<name>.yaml`` + "
            "``~/.spark/tasks/<name>.yaml``. Test-only."
        ),
    )
    overwrite: bool = Field(
        default=False,
        description=(
            "When True, the install replaces existing files at the target "
            "paths. Default False — refuses overwrite."
        ),
    )


class InstallResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    agent_name: str
    task_name: str
    agent_path: str
    task_path: str
    plugins_still_to_configure: list[str]
    secrets_still_to_populate: list[str]


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/", response_model=list[TemplateSummary])
async def list_all(
    _: Principal = Depends(require_viewer),
) -> list[TemplateSummary]:
    try:
        templates = list_templates()
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"template discovery failed: {type(exc).__name__}: {exc}",
        ) from exc
    return [TemplateSummary(**t.to_summary()) for t in templates]


@router.get("/{name}", response_model=TemplateDetail)
async def get_one(
    name: str,
    _: Principal = Depends(require_viewer),
) -> TemplateDetail:
    try:
        tpl = load_template(name)
    except TemplateNotFound as exc:
        raise HTTPException(status_code=404, detail=f"template {name!r} not found") from exc
    return TemplateDetail(
        **tpl.to_summary(),
        readme=tpl.readme,
        agent_yaml=tpl.agent_yaml,
        task_yaml=tpl.task_yaml,
        plugin_config_hints=tpl.plugin_config_hints,
    )


@router.post("/{name}/install", response_model=InstallResponse)
async def install_one(
    name: str,
    body: InstallRequest,
    principal: Principal = Depends(require_operator),
) -> InstallResponse:
    try:
        tpl = load_template(name)
    except TemplateNotFound as exc:
        raise HTTPException(status_code=404, detail=f"template {name!r} not found") from exc

    target_base = (body.target_dir or Path("~/.spark")).expanduser().resolve()
    agents_dir = target_base / "agents"
    tasks_dir = target_base / "tasks"
    agents_dir.mkdir(parents=True, exist_ok=True)
    tasks_dir.mkdir(parents=True, exist_ok=True)

    agent_target = agents_dir / f"{tpl.name}.yaml"
    task_target = tasks_dir / f"{tpl.name}.yaml"

    if not body.overwrite and (agent_target.exists() or task_target.exists()):
        raise HTTPException(
            status_code=409,
            detail=(
                f"refuses to overwrite existing files at {agent_target} / "
                f"{task_target}. Pass overwrite=true to replace."
            ),
        )

    # Defensive: verify the resolved targets are inside the target base.
    # This guards against a template name like ``../evil`` sneaking past
    # the registry (which it can't — names are directory names — but
    # belt + suspenders).
    for path in (agent_target, task_target):
        try:
            path.resolve().relative_to(target_base)
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail="template install target escapes target_dir",
            ) from exc

    # Copy the YAMLs verbatim — no substitution. The operator can edit
    # them in place if they want.
    shutil.copyfile(tpl.directory / "agent.yaml", agent_target)
    shutil.copyfile(tpl.directory / "task.yaml", task_target)

    # Register the agent + task in the DB so they appear in the
    # scheduler, chat, and run-history pages immediately.
    try:
        from spark.config.loader import load_agent, load_task  # noqa: PLC0415
        from spark.persistence.models import AgentRow, TaskRow  # noqa: PLC0415
        from spark.persistence.repositories import (  # noqa: PLC0415
            AgentRepository,
            TaskRepository,
        )
        from spark.utils.hashing import sha256_text  # noqa: PLC0415

        agent_obj = load_agent(agent_target)
        task_obj = load_task(task_target)
        async with session_scope() as session:
            await AgentRepository(session).upsert(
                AgentRow(
                    name=agent_obj.metadata.name,
                    description=agent_obj.spec.description or "",
                    config_hash=sha256_text(agent_obj.model_dump_json()),
                )
            )
            await TaskRepository(session).upsert(
                TaskRow(
                    name=task_obj.metadata.name,
                    agent_name=agent_obj.metadata.name,
                    mode=task_obj.spec.mode.value,
                    config_hash=sha256_text(task_obj.model_dump_json()),
                    config_path=str(task_target),
                    state="created",
                )
            )
    except Exception as exc:  # pragma: no cover — non-fatal
        import warnings  # noqa: PLC0415

        warnings.warn(f"template DB registration failed (non-fatal): {exc}", stacklevel=1)

    # Compute the "still to do" lists for the UI. We do this opportunistically
    # (no hard failure if the DB lookups aren't available).
    plugins_still_to_configure = await _plugins_still_needing_config(tpl.plugins_required)
    secrets_still_to_populate = await _secrets_still_needing_population(tpl.secrets_required)

    async with session_scope() as session:
        await AuditRepository(session).append(
            actor=principal.subject,
            kind="template.install",
            target=tpl.name,
            diff={
                "agent_path": str(agent_target),
                "task_path": str(task_target),
                "overwrite": body.overwrite,
            },
            reason=f"installed template {tpl.name!r}",
            severity="info",
        )

    # Derive the canonical names from the parsed YAML so the UI can
    # navigate straight to the newly-installed agent instead of the
    # plugin-config form.
    try:
        from spark.config.loader import load_agent, load_task  # noqa: PLC0415

        _agent_name = load_agent(agent_target).metadata.name
        _task_name = load_task(task_target).metadata.name
    except Exception:
        # Fall back to filename stems if the YAML can't be re-parsed
        # (already registered above, so this is conservative).
        _agent_name = agent_target.stem
        _task_name = task_target.stem

    return InstallResponse(
        agent_name=_agent_name,
        task_name=_task_name,
        agent_path=str(agent_target),
        task_path=str(task_target),
        plugins_still_to_configure=plugins_still_to_configure,
        secrets_still_to_populate=secrets_still_to_populate,
    )


class TemplateSaveRequest(BaseModel):
    """Create or update a template on disk."""

    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9][a-z0-9-]{0,62}[a-z0-9]$")
    agent_yaml: str = Field(min_length=10, max_length=50_000)
    task_yaml: str = Field(min_length=10, max_length=50_000)
    readme: str = Field(default="", max_length=50_000)
    plugin_config_hints: dict[str, Any] = Field(default_factory=dict)


@router.put("/{name}")
async def save_template(
    name: str,
    body: TemplateSaveRequest,
    principal: Principal = Depends(require_operator),
) -> dict[str, Any]:
    """Create or update a template on disk. Validates YAMLs before writing."""
    import json as _json  # noqa: PLC0415

    if body.name != name:
        raise HTTPException(status_code=400, detail="name in body must match URL")

    # Validate the YAMLs parse as Agent + Task.
    try:
        from ruamel.yaml import YAML  # noqa: PLC0415
        import io  # noqa: PLC0415
        from spark.config.models import Agent, Task  # noqa: PLC0415

        yaml = YAML(typ="safe")
        agent_raw = yaml.load(io.StringIO(body.agent_yaml))
        Agent.model_validate(agent_raw)
        task_raw = yaml.load(io.StringIO(body.task_yaml))
        Task.model_validate(task_raw)
    except Exception as exc:
        raise HTTPException(
            status_code=422, detail=f"YAML validation failed: {exc}"
        ) from exc

    # Write to the templates root (creates directory if needed).
    root = templates_root()
    tpl_dir = root / name
    tpl_dir.mkdir(parents=True, exist_ok=True)

    (tpl_dir / "agent.yaml").write_text(body.agent_yaml, encoding="utf-8")
    (tpl_dir / "task.yaml").write_text(body.task_yaml, encoding="utf-8")
    (tpl_dir / "README.md").write_text(
        body.readme or f"# {name}\n\nCustom template.\n", encoding="utf-8"
    )
    (tpl_dir / "plugin-config.hints.json").write_text(
        _json.dumps(body.plugin_config_hints, indent=2), encoding="utf-8"
    )

    _invalidate_cache()

    async with session_scope() as session:
        await AuditRepository(session).append(
            actor=principal.subject,
            kind="template.saved",
            target=name,
            severity="info",
        )

    return {"ok": True, "path": str(tpl_dir)}


@router.delete("/{name}")
async def delete_template(
    name: str,
    principal: Principal = Depends(require_admin),
) -> dict[str, bool]:
    """Delete a template from disk."""
    root = templates_root()
    tpl_dir = root / name
    if not tpl_dir.exists():
        raise HTTPException(status_code=404, detail="template not found")

    shutil.rmtree(tpl_dir)
    _invalidate_cache()

    async with session_scope() as session:
        await AuditRepository(session).append(
            actor=principal.subject,
            kind="template.deleted",
            target=name,
            severity="elevated",
        )

    return {"ok": True}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _plugins_still_needing_config(plugin_names: list[str]) -> list[str]:
    """Return the subset of ``plugin_names`` that don't have a configured row yet.

    Looks up ``plugin_configs`` and treats an empty / missing config as
    "still to configure". Non-fatal on DB error.
    """
    from sqlalchemy import select

    from spark.persistence.learning_models import PluginConfigRow

    try:
        async with session_scope() as session:
            result = await session.execute(
                select(PluginConfigRow.plugin_name, PluginConfigRow.config_json)
                .where(PluginConfigRow.plugin_name.in_(plugin_names))
            )
            configured: set[str] = set()
            for row_name, raw in result.all():
                if raw and raw.strip() not in ("", "{}"):
                    configured.add(row_name)
    except Exception:  # pragma: no cover — DB unavailable
        return list(plugin_names)

    return [p for p in plugin_names if p not in configured]


async def _secrets_still_needing_population(names: list[str]) -> list[str]:
    """Return the subset of ``names`` not in the active secret manager.

    Consults the process-scoped manager (vault + env fallback) via
    :func:`spark.runtime.get_secret_manager`.
    """
    try:
        from spark.runtime import get_secret_manager

        mgr = get_secret_manager()
        present = set(mgr.list_names())
    except Exception:  # pragma: no cover
        return list(names)
    return [n for n in names if n not in present]


# Unused for now but available for future "dismiss install" audit.
_LAST_INSTALL_AT: datetime | None = None


def _note_install() -> None:  # pragma: no cover
    global _LAST_INSTALL_AT
    _LAST_INSTALL_AT = datetime.now(tz=UTC)
