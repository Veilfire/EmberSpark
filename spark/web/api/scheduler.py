"""Agent / task / run / schedule routes (+ F5 webhooks + simulation)."""

from __future__ import annotations

import re
import secrets as _secrets
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select

from spark.logging import get_logger
from spark.persistence.db import session_scope
from spark.persistence.learning_models import TriggerRow
from spark.persistence.learning_repos import AuditRepository, TriggerRepository
from spark.persistence.models import (
    AgentRow,
    ScheduleRow,
    TaskRow,
    TaskRunRow,
)
from spark.web.auth import Principal, require_admin, require_operator, require_viewer
from spark.web.schemas import (
    AgentSummary,
    TaskRunSummary,
    TaskSummary,
    TaskTriggerRequest,
)

log = get_logger("spark.web.scheduler")
_ID_PATTERN = re.compile(r"^[a-zA-Z0-9._-]{1,128}$")

router = APIRouter()


@router.get("/attention")
async def needs_attention(
    _: Principal = Depends(require_viewer),
) -> dict[str, Any]:
    """Count of things that need operator attention.

    Aggregates DLQ'd tasks, pending skill reviews, failed runs in last 24h,
    expiring IP grants, and expiring forensic captures.
    """
    from datetime import datetime, timedelta, timezone  # noqa: PLC0415

    now = datetime.now(tz=timezone.utc)
    since = now - timedelta(hours=24)

    async with session_scope() as session:
        # Failed runs last 24h
        fr = await session.execute(
            select(TaskRunRow).where(
                TaskRunRow.state == "failed", TaskRunRow.started_at >= since
            )
        )
        failed_runs = len(list(fr.scalars().all()))

        # DLQ tasks
        from spark.persistence.learning_models import (  # noqa: PLC0415
            ForensicCaptureRow,
            InternalNetworkGrantRow,
            SkillReviewRow,
        )

        try:
            sr = await session.execute(
                select(SkillReviewRow).where(SkillReviewRow.status == "pending")
            )
            pending_skills = len(list(sr.scalars().all()))
        except Exception:
            pending_skills = 0

        # Expiring grants (within 1 hour)
        try:
            expiring_soon = now + timedelta(hours=1)
            eg = await session.execute(
                select(InternalNetworkGrantRow).where(
                    InternalNetworkGrantRow.expires_at <= expiring_soon,
                    InternalNetworkGrantRow.expires_at > now,
                )
            )
            expiring_grants = len(list(eg.scalars().all()))
        except Exception:
            expiring_grants = 0

        # Expiring forensic captures (within 24h)
        try:
            ef = await session.execute(
                select(ForensicCaptureRow).where(
                    ForensicCaptureRow.expires_at <= now + timedelta(hours=24),
                    ForensicCaptureRow.expires_at > now,
                    ForensicCaptureRow.wiped_at.is_(None),
                )
            )
            expiring_forensic = len(list(ef.scalars().all()))
        except Exception:
            expiring_forensic = 0

        # DLQ tasks
        try:
            dq = await session.execute(
                select(TaskRunRow).where(TaskRunRow.state == "dlq")
            )
            dlq = len(list(dq.scalars().all()))
        except Exception:
            dlq = 0

    total = failed_runs + pending_skills + expiring_grants + expiring_forensic + dlq
    return {
        "total": total,
        "failed_runs_24h": failed_runs,
        "pending_skills": pending_skills,
        "expiring_grants": expiring_grants,
        "expiring_forensic": expiring_forensic,
        "dlq_tasks": dlq,
    }


@router.get("/agents", response_model=list[AgentSummary])
async def list_agents(_: Principal = Depends(require_viewer)) -> list[AgentSummary]:
    async with session_scope() as session:
        result = await session.execute(select(AgentRow))
        rows = list(result.scalars().all())
    return [
        AgentSummary(
            name=r.name,
            description=r.description,
            updated_at=r.updated_at,
        )
        for r in rows
    ]


@router.get("/agents/{agent_name}")
async def get_agent_detail(
    agent_name: str, _: Principal = Depends(require_viewer)
) -> dict[str, Any]:
    """Rich agent detail: config, provider, stats, tasks, cost, health."""
    from datetime import datetime, timedelta, timezone  # noqa: PLC0415
    from pathlib import Path  # noqa: PLC0415
    from spark.persistence.learning_models import (  # noqa: PLC0415
        CostEventRow,
        PlaybookRow,
    )
    from spark.persistence.models import LongTermMemoryIndexRow  # noqa: PLC0415

    async with session_scope() as session:
        agent_row = await session.get(AgentRow, agent_name)
    if agent_row is None:
        raise HTTPException(status_code=404, detail="agent not found")

    # Parse agent YAML for full config.
    agent_path = Path(f"~/.spark/agents/{agent_name}.yaml").expanduser()
    provider_info: dict[str, Any] = {}
    plugins: list[str] = []
    grants: list[str] = []
    memory_cfg: dict[str, Any] = {}
    budgets: dict[str, Any] = {}
    sandbox_cfg: dict[str, Any] = {}
    if agent_path.exists():
        try:
            from spark.config.loader import load_agent  # noqa: PLC0415

            agent = load_agent(agent_path)
            p = agent.spec.runtime.provider
            provider_info = {
                "type": p.type,
                "model": p.model,
                "api_key_ref": getattr(p, "api_key_ref", None),
                "base_url": getattr(p, "base_url", None),
                "temperature": p.temperature,
            }
            plugins = sorted(agent.spec.plugins.allow)
            grants = sorted(g.value for g in agent.spec.permissions.grants)
            rc = agent.spec.runtime
            budgets = {
                "max_iterations": rc.max_iterations,
                "max_model_calls": rc.max_model_calls,
                "max_tool_calls": rc.max_tool_calls,
                "max_runtime_seconds": rc.max_runtime_seconds,
            }
            ltm = agent.spec.memory.long_term_memory
            sharing = getattr(agent.spec.memory, "sharing", None)
            memory_cfg = {
                "task_memory": agent.spec.memory.task_memory,
                "session_memory": agent.spec.memory.session_memory.enabled if agent.spec.memory.session_memory else False,
                "long_term_memory": ltm.enabled if ltm else False,
                "namespace": ltm.namespace if ltm else None,
                "collection": ltm.collection if ltm else None,
                "sharing": {
                    "read_global": getattr(sharing, "read_global", False),
                    "write_global": getattr(sharing, "write_global", False),
                    "max_cross_scope_sensitivity": getattr(
                        sharing, "max_cross_scope_sensitivity", "moderate"
                    ),
                },
            }
            sb = agent.spec.permissions.sandbox
            sandbox_cfg = {
                "enabled": sb.enabled,
                "backend": sb.backend.value if hasattr(sb.backend, "value") else str(sb.backend),
                "cpu_seconds": sb.cpu_seconds,
                "memory_mb": sb.memory_mb,
            }
        except Exception:
            pass

    # Tasks for this agent.
    async with session_scope() as session:
        tasks_result = await session.execute(
            select(TaskRow).where(TaskRow.agent_name == agent_name)
        )
        task_rows = list(tasks_result.scalars().all())

    tasks = [
        {"name": t.name, "mode": t.mode, "state": t.state, "updated_at": str(t.updated_at)}
        for t in task_rows
    ]

    # Run stats (last 7 days).
    since = datetime.now(tz=timezone.utc) - timedelta(days=7)
    async with session_scope() as session:
        runs_result = await session.execute(
            select(TaskRunRow).where(
                TaskRunRow.agent_name == agent_name,
                TaskRunRow.started_at >= since,
            )
        )
        runs = list(runs_result.scalars().all())

    completed = sum(1 for r in runs if r.state == "completed")
    failed = sum(1 for r in runs if r.state == "failed")
    run_stats = {
        "total_7d": len(runs),
        "completed_7d": completed,
        "failed_7d": failed,
        "success_rate_7d": round(completed / len(runs), 2) if runs else None,
    }

    # Cost (last 7 days).
    async with session_scope() as session:
        cost_result = await session.execute(
            select(CostEventRow).where(
                CostEventRow.agent_name == agent_name,
                CostEventRow.recorded_at >= since,
            )
        )
        cost_rows = list(cost_result.scalars().all())

    total_cost = sum(r.total_cost_usd or 0 for r in cost_rows)
    total_tokens = sum(r.total_tokens or 0 for r in cost_rows)

    # Playbook count.
    async with session_scope() as session:
        pb_result = await session.execute(
            select(PlaybookRow).where(PlaybookRow.agent_name == agent_name)
        )
        playbook_count = len(list(pb_result.scalars().all()))

    # Memory count.
    memory_count = 0
    async with session_scope() as session:
        mem_result = await session.execute(
            select(LongTermMemoryIndexRow).where(
                LongTermMemoryIndexRow.agent_name == agent_name
            )
        )
        memory_count = len(list(mem_result.scalars().all()))

    # Active persona.
    from spark.persistence.learning_models import PersonaRow  # noqa: PLC0415

    persona: dict[str, Any] | None = None
    async with session_scope() as session:
        persona_result = await session.execute(
            select(PersonaRow).where(PersonaRow.is_active == True)  # noqa: E712
        )
        active = persona_result.scalars().first()
        if active:
            persona = {
                "persona_id": active.persona_id,
                "name": active.name,
                "tone": active.tone,
            }

    # Sandbox health.
    sandbox_ok = False
    sandbox_backend = "unknown"
    try:
        from spark.sandbox.executor import check_available  # noqa: PLC0415

        sandbox_backend = check_available()
        sandbox_ok = True
    except Exception:
        pass

    # Secret availability for provider key.
    key_available = False
    key_name = provider_info.get("api_key_ref")
    if key_name:
        try:
            from spark.runtime import get_secret_manager  # noqa: PLC0415

            key_available = get_secret_manager().available(key_name)
        except Exception:
            pass
    elif provider_info.get("type") == "ollama":
        key_available = True  # Ollama doesn't need a key.

    return {
        "name": agent_row.name,
        "description": agent_row.description,
        "created_at": str(agent_row.created_at),
        "updated_at": str(agent_row.updated_at),
        "provider": provider_info,
        "provider_key_available": key_available,
        "plugins": plugins,
        "grants": grants,
        "budgets": budgets,
        "memory": memory_cfg,
        "sandbox": sandbox_cfg,
        "tasks": tasks,
        "run_stats": run_stats,
        "cost_7d_usd": round(total_cost, 4),
        "tokens_7d": total_tokens,
        "playbook_count": playbook_count,
        "memory_count": memory_count,
        "persona": persona,
        "health": {
            "sandbox_ok": sandbox_ok,
            "sandbox_backend": sandbox_backend,
            "provider_key_available": key_available,
        },
    }


@router.get("/agents/{agent_name}/yaml")
async def get_agent_yaml(
    agent_name: str, _: Principal = Depends(require_viewer)
) -> dict[str, Any]:
    """Return the raw agent YAML as text + parsed provider info."""
    from pathlib import Path  # noqa: PLC0415

    agent_path = Path(f"~/.spark/agents/{agent_name}.yaml").expanduser()
    if not agent_path.exists():
        raise HTTPException(status_code=404, detail="agent YAML not found on disk")
    raw = agent_path.read_text(encoding="utf-8")

    # Parse provider info for the UI.
    provider_info: dict[str, Any] = {}
    try:
        from spark.config.loader import load_agent  # noqa: PLC0415

        agent = load_agent(agent_path)
        p = agent.spec.runtime.provider
        provider_info = {
            "type": p.type,
            "model": p.model,
            "api_key_ref": getattr(p, "api_key_ref", None),
            "base_url": getattr(p, "base_url", None),
            "temperature": p.temperature,
        }
    except Exception:
        pass

    return {"yaml": raw, "provider": provider_info}


class ProviderUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: str = Field(description="anthropic | openai | openrouter | ollama")
    model: str = Field(min_length=1, max_length=256)
    api_key_ref: str | None = Field(default=None, max_length=128)
    base_url: str | None = Field(default=None, max_length=512)
    temperature: float = Field(default=0.2, ge=0.0, le=2.0)


@router.put("/agents/{agent_name}/provider")
async def update_agent_provider(
    agent_name: str,
    body: ProviderUpdateRequest,
    principal: Principal = Depends(require_operator),
) -> dict[str, Any]:
    """Update just the provider block in an agent YAML on disk."""
    from pathlib import Path  # noqa: PLC0415

    agent_path = Path(f"~/.spark/agents/{agent_name}.yaml").expanduser()
    if not agent_path.exists():
        raise HTTPException(status_code=404, detail="agent YAML not found on disk")

    try:
        from ruamel.yaml import YAML  # noqa: PLC0415

        yaml = YAML()
        yaml.preserve_quotes = True
        with agent_path.open("r") as f:
            data = yaml.load(f)

        # Build the new provider block.
        new_provider: dict[str, Any] = {
            "type": body.type,
            "model": body.model,
            "temperature": body.temperature,
        }
        if body.api_key_ref:
            new_provider["api_key_ref"] = body.api_key_ref
        if body.base_url:
            new_provider["base_url"] = body.base_url

        data["spec"]["runtime"]["provider"] = new_provider

        with agent_path.open("w") as f:
            yaml.dump(data, f)

    except Exception as exc:
        raise HTTPException(
            status_code=500, detail=f"failed to update YAML: {exc}"
        ) from exc

    # Update the DB row's config hash so the scheduler sees the change.
    try:
        from spark.config.loader import load_agent  # noqa: PLC0415
        from spark.persistence.repositories import AgentRepository  # noqa: PLC0415
        from spark.utils.hashing import sha256_text  # noqa: PLC0415

        agent = load_agent(agent_path)
        async with session_scope() as session:
            await AgentRepository(session).upsert(
                AgentRow(
                    name=agent.metadata.name,
                    description=agent.spec.description or "",
                    config_hash=sha256_text(agent.model_dump_json()),
                )
            )
    except Exception:
        pass

    async with session_scope() as session:
        await AuditRepository(session).append(
            actor=principal.subject,
            kind="agent.provider_updated",
            target=agent_name,
            diff={"type": body.type, "model": body.model},
            severity="info",
        )

    return {"ok": True, "provider": new_provider}


class MemorySharingUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    read_global: bool
    write_global: bool
    max_cross_scope_sensitivity: str = Field(default="moderate")


@router.put("/agents/{agent_name}/memory-sharing")
async def update_agent_memory_sharing(
    agent_name: str,
    body: MemorySharingUpdateRequest,
    principal: Principal = Depends(require_operator),
) -> dict[str, Any]:
    """Patch the agent YAML's ``spec.memory.sharing`` block in place."""
    from pathlib import Path  # noqa: PLC0415

    if body.max_cross_scope_sensitivity not in ("low", "moderate", "high"):
        raise HTTPException(
            status_code=400,
            detail="max_cross_scope_sensitivity must be low|moderate|high",
        )

    agent_path = Path(f"~/.spark/agents/{agent_name}.yaml").expanduser()
    if not agent_path.exists():
        raise HTTPException(status_code=404, detail="agent YAML not found on disk")

    try:
        from ruamel.yaml import YAML  # noqa: PLC0415

        yaml = YAML()
        yaml.preserve_quotes = True
        with agent_path.open("r") as f:
            data = yaml.load(f)

        mem = data["spec"].setdefault("memory", {})
        mem["sharing"] = {
            "read_global": body.read_global,
            "write_global": body.write_global,
            "max_cross_scope_sensitivity": body.max_cross_scope_sensitivity,
        }

        with agent_path.open("w") as f:
            yaml.dump(data, f)
    except Exception as exc:
        raise HTTPException(
            status_code=500, detail=f"failed to update YAML: {exc}"
        ) from exc

    async with session_scope() as session:
        await AuditRepository(session).append(
            actor=principal.subject,
            kind="agent.memory_sharing_updated",
            target=agent_name,
            diff=body.model_dump(),
            severity="elevated",
        )

    return {"ok": True}


class LongTermMemoryUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool
    namespace: str | None = Field(default=None, max_length=128)
    collection: str | None = Field(default=None, max_length=128)


@router.put("/agents/{agent_name}/long-term-memory")
async def update_agent_long_term_memory(
    agent_name: str,
    body: LongTermMemoryUpdateRequest,
    principal: Principal = Depends(require_operator),
) -> dict[str, Any]:
    """Enable / disable long-term memory and (optionally) set namespace + collection.

    Patches ``spec.memory.long_term_memory`` on the agent YAML in place. If
    the block was absent and ``enabled=true``, we seed defaults using the
    agent name as both namespace and collection — operators can rename
    them by re-POSTing with explicit values.
    """
    from pathlib import Path  # noqa: PLC0415

    agent_path = Path(f"~/.spark/agents/{agent_name}.yaml").expanduser()
    if not agent_path.exists():
        raise HTTPException(status_code=404, detail="agent YAML not found on disk")

    try:
        from ruamel.yaml import YAML  # noqa: PLC0415

        yaml = YAML()
        yaml.preserve_quotes = True
        with agent_path.open("r") as f:
            data = yaml.load(f)

        mem = data["spec"].setdefault("memory", {})
        existing = mem.get("long_term_memory") or {}
        ns = body.namespace or existing.get("namespace") or agent_name
        col = body.collection or existing.get("collection") or agent_name
        mem["long_term_memory"] = {
            **existing,
            "enabled": body.enabled,
            "namespace": ns,
            "collection": col,
        }

        with agent_path.open("w") as f:
            yaml.dump(data, f)
    except Exception as exc:
        raise HTTPException(
            status_code=500, detail=f"failed to update YAML: {exc}"
        ) from exc

    async with session_scope() as session:
        await AuditRepository(session).append(
            actor=principal.subject,
            kind="agent.long_term_memory_updated",
            target=agent_name,
            diff=body.model_dump(),
            severity="elevated",
        )

    return {"ok": True}


class AgentYamlUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    yaml: str = Field(min_length=10, max_length=50_000)


@router.put("/agents/{agent_name}/yaml")
async def update_agent_yaml(
    agent_name: str,
    body: AgentYamlUpdateRequest,
    principal: Principal = Depends(require_admin),
) -> dict[str, bool]:
    """Overwrite the agent YAML on disk (freeform edit). Admin only."""
    from pathlib import Path  # noqa: PLC0415

    agent_path = Path(f"~/.spark/agents/{agent_name}.yaml").expanduser()
    if not agent_path.exists():
        raise HTTPException(status_code=404, detail="agent YAML not found on disk")

    # Validate the YAML parses as an Agent before writing.
    try:
        from spark.config.loader import load_agent  # noqa: PLC0415
        from spark.config.models import Agent  # noqa: PLC0415
        from ruamel.yaml import YAML  # noqa: PLC0415
        import io  # noqa: PLC0415

        yaml = YAML(typ="safe")
        raw = yaml.load(io.StringIO(body.yaml))
        Agent.model_validate(raw)
    except Exception as exc:
        raise HTTPException(
            status_code=422, detail=f"YAML validation failed: {exc}"
        ) from exc

    agent_path.write_text(body.yaml, encoding="utf-8")

    # Re-register in DB.
    try:
        from spark.persistence.repositories import AgentRepository  # noqa: PLC0415
        from spark.utils.hashing import sha256_text  # noqa: PLC0415

        agent = load_agent(agent_path)
        async with session_scope() as session:
            await AgentRepository(session).upsert(
                AgentRow(
                    name=agent.metadata.name,
                    description=agent.spec.description or "",
                    config_hash=sha256_text(agent.model_dump_json()),
                )
            )
    except Exception:
        pass

    async with session_scope() as session:
        await AuditRepository(session).append(
            actor=principal.subject,
            kind="agent.yaml_updated",
            target=agent_name,
            severity="elevated",
        )

    return {"ok": True}


class TaskScheduleSubmit(BaseModel):
    """Schedule fields submitted via the task creator. Mirrors the YAML
    ``CronSchedule`` / ``IntervalSchedule`` shapes but accepts ISO-8601
    strings for the timestamps so the JSON payload is portable."""

    model_config = ConfigDict(extra="forbid")
    type: Literal["cron", "interval"]
    expression: str = Field(min_length=1, max_length=128)
    timezone: str = Field(default="UTC", max_length=64)
    start_at: datetime | None = None
    end_at: datetime | None = None


class TaskBudgetSubmit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_runtime_seconds: int | None = Field(default=None, ge=1, le=86_400)
    max_model_calls: int | None = Field(default=None, ge=1, le=500)
    max_tool_calls: int | None = Field(default=None, ge=0, le=500)
    max_tokens_per_run: int | None = Field(default=None, ge=1, le=10_000_000)


class TaskForensicSubmit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    reason: str = Field(default="", max_length=500)
    ttl_hours: int = Field(default=168, ge=1, le=720)


class TaskCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=128)
    agent: str = Field(min_length=1, max_length=128)
    mode: Literal["one_shot", "recurring", "perpetual"]
    objective: str = Field(min_length=1, max_length=8000)
    inputs: dict[str, str | int | float | bool] = Field(default_factory=dict)
    schedule: TaskScheduleSubmit | None = None
    budgets: TaskBudgetSubmit | None = None
    forensic: TaskForensicSubmit | None = None
    auto_start: bool = False

    @field_validator("name", "agent")
    @classmethod
    def _slug(cls, v: str) -> str:
        if not _ID_PATTERN.match(v):
            raise ValueError("must match ^[a-zA-Z0-9._-]{1,128}$")
        return v


@router.post("/tasks")
async def create_task(
    body: TaskCreateRequest, principal: Principal = Depends(require_operator)
) -> dict[str, Any]:
    """Create a task ad-hoc from the web UI.

    Writes the task to ``~/.spark/tasks/{name}.yaml`` so it lives
    alongside operator-edited YAML, then validates by round-tripping
    through ``load_task``, registers in the DB, and optionally
    schedules it.
    """
    from pathlib import Path  # noqa: PLC0415

    from spark.config.enums import TaskMode  # noqa: PLC0415
    from spark.config.loader import load_task  # noqa: PLC0415
    from spark.config.models import (  # noqa: PLC0415
        BudgetOverrides,
        CronSchedule,
        ForensicSpec,
        IntervalSchedule,
        Metadata,
        Task,
        TaskSpec,
    )
    from spark.persistence.models import AgentRow, TaskRow  # noqa: PLC0415
    from spark.persistence.repositories import TaskRepository  # noqa: PLC0415
    from spark.utils.hashing import sha256_text  # noqa: PLC0415

    # 1. Agent existence + name uniqueness.
    async with session_scope() as session:
        if await session.get(AgentRow, body.agent) is None:
            raise HTTPException(
                status_code=400, detail=f"agent {body.agent!r} not found"
            )
        if await session.get(TaskRow, body.name) is not None:
            raise HTTPException(
                status_code=409, detail=f"task {body.name!r} already exists"
            )

    # 2. Build the schedule sub-model (if any). Pydantic enforces the
    # mode/schedule constraints when we assemble the Task below.
    schedule_obj: CronSchedule | IntervalSchedule | None = None
    if body.schedule is not None:
        if body.schedule.type == "cron":
            schedule_obj = CronSchedule(
                expression=body.schedule.expression,
                timezone=body.schedule.timezone,
                start_at=body.schedule.start_at,
                end_at=body.schedule.end_at,
            )
        else:
            try:
                seconds = int(body.schedule.expression)
            except ValueError as exc:
                raise HTTPException(
                    status_code=400,
                    detail="interval schedule.expression must be the integer second count",
                ) from exc
            if seconds <= 0 or seconds > 86_400 * 7:
                raise HTTPException(
                    status_code=400,
                    detail="interval seconds must be 1..604800",
                )
            schedule_obj = IntervalSchedule(
                seconds=seconds,
                timezone=body.schedule.timezone,
                start_at=body.schedule.start_at,
                end_at=body.schedule.end_at,
            )

    # 3. Build the Task pydantic model. Mode-validator raises on bad combos.
    try:
        task_obj = Task(
            metadata=Metadata(name=body.name),
            spec=TaskSpec(
                agent=body.agent,
                mode=TaskMode(body.mode),
                schedule=schedule_obj,
                objective=body.objective,
                inputs=body.inputs,
                budgets=BudgetOverrides(
                    **(body.budgets.model_dump(exclude_none=True) if body.budgets else {})
                ),
                forensic=(
                    ForensicSpec(
                        enabled=body.forensic.enabled,
                        reason=body.forensic.reason,
                        ttl_hours=body.forensic.ttl_hours,
                    )
                    if body.forensic
                    else ForensicSpec()
                ),
            ),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # 4. Write canonical YAML to ~/.spark/tasks/{name}.yaml.
    tasks_dir = Path("~/.spark/tasks").expanduser()
    tasks_dir.mkdir(parents=True, exist_ok=True)
    target = tasks_dir / f"{body.name}.yaml"

    try:
        from ruamel.yaml import YAML  # noqa: PLC0415

        yaml = YAML()
        yaml.default_flow_style = False
        with target.open("w") as f:
            yaml.dump(task_obj.model_dump(mode="json", exclude_none=True), f)
    except Exception as exc:
        raise HTTPException(
            status_code=500, detail=f"failed to write task YAML: {exc}"
        ) from exc

    # 5. Round-trip through load_task to confirm disk shape parses.
    try:
        reloaded = load_task(target)
    except Exception as exc:
        # Roll back the file write so a broken YAML doesn't litter the
        # tasks directory.
        try:
            target.unlink()
        except OSError:
            pass
        raise HTTPException(
            status_code=500,
            detail=f"task YAML failed to round-trip: {exc}",
        ) from exc

    # 6. Upsert TaskRow.
    initial_state = "scheduled" if (body.auto_start and schedule_obj is not None) else "created"
    async with session_scope() as session:
        repo = TaskRepository(session)
        await repo.upsert(
            TaskRow(
                name=reloaded.metadata.name,
                agent_name=reloaded.spec.agent,
                mode=reloaded.spec.mode.value,
                config_hash=sha256_text(reloaded.model_dump_json()),
                config_path=str(target),
                state=initial_state,
            )
        )
        await AuditRepository(session).append(
            actor=principal.subject,
            kind="task.created",
            target=body.name,
            diff={
                "agent": body.agent,
                "mode": body.mode,
                "has_schedule": schedule_obj is not None,
                "has_forensic": bool(body.forensic and body.forensic.enabled),
                "has_budget_overrides": body.budgets is not None,
                "auto_start": body.auto_start,
            },
            reason="ad-hoc create from web UI",
            severity="info",
        )

    # 7. If auto_start AND we have a schedule, register with the scheduler.
    scheduled = False
    if body.auto_start and schedule_obj is not None:
        try:
            from spark.scheduler import get_scheduler  # noqa: PLC0415

            sched = get_scheduler()
            if sched is not None:
                from spark.config.loader import load_agent  # noqa: PLC0415

                agent_path = (
                    Path("~/.spark/agents").expanduser() / f"{body.agent}.yaml"
                )
                if agent_path.exists():
                    agent_obj = load_agent(agent_path)
                    await sched.schedule_task(agent_obj, reloaded)
                    scheduled = True
        except Exception as exc:  # pragma: no cover — best-effort
            log.warning(
                "task_create_schedule_failed", task=body.name, error=str(exc)
            )

    return {
        "ok": True,
        "name": body.name,
        "config_path": str(target),
        "scheduled": scheduled,
    }


@router.get("/tasks", response_model=list[TaskSummary])
async def list_tasks(_: Principal = Depends(require_viewer)) -> list[TaskSummary]:
    async with session_scope() as session:
        result = await session.execute(select(TaskRow))
        rows = list(result.scalars().all())
    return [
        TaskSummary(
            name=r.name,
            agent_name=r.agent_name,
            mode=r.mode,
            state=r.state,
            created_at=r.created_at,
            updated_at=r.updated_at,
        )
        for r in rows
    ]


@router.get("/tasks/{task_name}", response_model=TaskSummary)
async def get_task(
    task_name: str, _: Principal = Depends(require_viewer)
) -> TaskSummary:
    async with session_scope() as session:
        row = await session.get(TaskRow, task_name)
    if row is None:
        raise HTTPException(status_code=404, detail="task not found")
    return TaskSummary(
        name=row.name,
        agent_name=row.agent_name,
        mode=row.mode,
        state=row.state,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@router.get("/tasks/{task_name}/full")
async def get_task_full(
    task_name: str, _: Principal = Depends(require_viewer)
) -> dict[str, Any]:
    """Return the full task spec for the editor modal.

    The web UI's task creator/editor uses this to pre-populate fields.
    Reads the operator-edited YAML on disk so the response reflects any
    out-of-band edits, not just the DB cache.
    """
    from pathlib import Path  # noqa: PLC0415

    from spark.config.loader import load_task  # noqa: PLC0415

    if not _ID_PATTERN.match(task_name):
        raise HTTPException(status_code=400, detail="invalid task_name")

    async with session_scope() as session:
        row = await session.get(TaskRow, task_name)
    if row is None:
        raise HTTPException(status_code=404, detail="task not found")

    config_path = (
        Path(row.config_path)
        if row.config_path
        else Path(f"~/.spark/tasks/{task_name}.yaml").expanduser()
    )
    if not config_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"task YAML missing on disk at {config_path}",
        )

    try:
        task = load_task(config_path)
    except Exception as exc:
        raise HTTPException(
            status_code=500, detail=f"failed to parse task YAML: {exc}"
        ) from exc

    schedule_dict: dict[str, Any] | None = None
    if task.spec.schedule is not None:
        s = task.spec.schedule
        from spark.config.models import IntervalSchedule  # noqa: PLC0415

        if isinstance(s, IntervalSchedule):
            expr = str(s.seconds)
        else:
            expr = s.expression
        schedule_dict = {
            "type": s.type,
            "expression": expr,
            "timezone": s.timezone,
            "start_at": s.start_at.isoformat() if s.start_at else None,
            "end_at": s.end_at.isoformat() if s.end_at else None,
        }

    return {
        "name": task.metadata.name,
        "agent": task.spec.agent,
        "mode": task.spec.mode.value,
        "objective": task.spec.objective,
        "inputs": dict(task.spec.inputs),
        "schedule": schedule_dict,
        "budgets": {
            "max_runtime_seconds": task.spec.budgets.max_runtime_seconds,
            "max_model_calls": task.spec.budgets.max_model_calls,
            "max_tool_calls": task.spec.budgets.max_tool_calls,
            "max_tokens_per_run": task.spec.budgets.max_tokens_per_run,
        },
        "forensic": {
            "enabled": task.spec.forensic.enabled,
            "reason": task.spec.forensic.reason,
            "ttl_hours": task.spec.forensic.ttl_hours,
        },
        "state": row.state,
        "config_path": str(config_path),
    }


@router.put("/tasks/{task_name}")
async def update_task(
    task_name: str,
    body: TaskCreateRequest,
    principal: Principal = Depends(require_operator),
) -> dict[str, Any]:
    """Update an existing task in place.

    Reuses ``TaskCreateRequest`` for shape parity with create. The
    ``name`` field on the body is ignored; the URL path is authoritative
    (renames are not supported — delete + recreate instead). Refuses to
    edit while a run is in flight (state == 'running'). Re-schedules
    automatically if the schedule block changed.
    """
    from pathlib import Path  # noqa: PLC0415

    from spark.config.enums import TaskMode  # noqa: PLC0415
    from spark.config.loader import load_task  # noqa: PLC0415
    from spark.config.models import (  # noqa: PLC0415
        BudgetOverrides,
        CronSchedule,
        ForensicSpec,
        IntervalSchedule,
        Metadata,
        Task,
        TaskSpec,
    )
    from spark.persistence.models import AgentRow  # noqa: PLC0415
    from spark.persistence.repositories import TaskRepository  # noqa: PLC0415
    from spark.utils.hashing import sha256_text  # noqa: PLC0415

    if not _ID_PATTERN.match(task_name):
        raise HTTPException(status_code=400, detail="invalid task_name")

    # Refuse rename via the body — clearer than silently overriding.
    if body.name != task_name:
        raise HTTPException(
            status_code=400,
            detail=(
                "task name in body must match URL — renames are not "
                "supported. Stop the task and create a new one if you "
                "need a different name."
            ),
        )

    async with session_scope() as session:
        existing = await session.get(TaskRow, task_name)
        if existing is None:
            raise HTTPException(status_code=404, detail="task not found")
        if existing.state == "running":
            raise HTTPException(
                status_code=409,
                detail=(
                    "task is currently running — wait for the run to "
                    "finish or stop it before editing"
                ),
            )
        if await session.get(AgentRow, body.agent) is None:
            raise HTTPException(
                status_code=400, detail=f"agent {body.agent!r} not found"
            )
        previous_agent = existing.agent_name

    # Build schedule sub-model identically to create.
    schedule_obj: CronSchedule | IntervalSchedule | None = None
    if body.schedule is not None:
        if body.schedule.type == "cron":
            schedule_obj = CronSchedule(
                expression=body.schedule.expression,
                timezone=body.schedule.timezone,
                start_at=body.schedule.start_at,
                end_at=body.schedule.end_at,
            )
        else:
            try:
                seconds = int(body.schedule.expression)
            except ValueError as exc:
                raise HTTPException(
                    status_code=400,
                    detail="interval schedule.expression must be the integer second count",
                ) from exc
            if seconds <= 0 or seconds > 86_400 * 7:
                raise HTTPException(
                    status_code=400,
                    detail="interval seconds must be 1..604800",
                )
            schedule_obj = IntervalSchedule(
                seconds=seconds,
                timezone=body.schedule.timezone,
                start_at=body.schedule.start_at,
                end_at=body.schedule.end_at,
            )

    try:
        task_obj = Task(
            metadata=Metadata(name=task_name),
            spec=TaskSpec(
                agent=body.agent,
                mode=TaskMode(body.mode),
                schedule=schedule_obj,
                objective=body.objective,
                inputs=body.inputs,
                budgets=BudgetOverrides(
                    **(body.budgets.model_dump(exclude_none=True) if body.budgets else {})
                ),
                forensic=(
                    ForensicSpec(
                        enabled=body.forensic.enabled,
                        reason=body.forensic.reason,
                        ttl_hours=body.forensic.ttl_hours,
                    )
                    if body.forensic
                    else ForensicSpec()
                ),
            ),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # Write to disk. Back up the previous YAML so a parse failure can
    # roll back without leaving a half-written file.
    tasks_dir = Path("~/.spark/tasks").expanduser()
    target = tasks_dir / f"{task_name}.yaml"
    backup: bytes | None = None
    if target.exists():
        try:
            backup = target.read_bytes()
        except OSError:
            backup = None

    try:
        from ruamel.yaml import YAML  # noqa: PLC0415

        yaml = YAML()
        yaml.default_flow_style = False
        with target.open("w") as f:
            yaml.dump(task_obj.model_dump(mode="json", exclude_none=True), f)
    except Exception as exc:
        raise HTTPException(
            status_code=500, detail=f"failed to write task YAML: {exc}"
        ) from exc

    try:
        reloaded = load_task(target)
    except Exception as exc:
        if backup is not None:
            try:
                target.write_bytes(backup)
            except OSError:  # pragma: no cover
                pass
        raise HTTPException(
            status_code=500,
            detail=f"task YAML failed to round-trip: {exc}",
        ) from exc

    # Detect security-relevant changes for audit severity.
    agent_changed = previous_agent != reloaded.spec.agent
    severity = "elevated" if agent_changed else "info"

    async with session_scope() as session:
        repo = TaskRepository(session)
        await repo.upsert(
            TaskRow(
                name=reloaded.metadata.name,
                agent_name=reloaded.spec.agent,
                mode=reloaded.spec.mode.value,
                config_hash=sha256_text(reloaded.model_dump_json()),
                config_path=str(target),
                state=existing.state,  # preserve current state
            )
        )
        await AuditRepository(session).append(
            actor=principal.subject,
            kind="task.updated",
            target=task_name,
            diff={
                "agent_before": previous_agent,
                "agent_after": reloaded.spec.agent,
                "agent_changed": agent_changed,
                "mode": body.mode,
                "has_schedule": schedule_obj is not None,
                "has_forensic": bool(body.forensic and body.forensic.enabled),
                "has_budget_overrides": body.budgets is not None,
            },
            reason="ad-hoc edit from web UI",
            severity=severity,
        )

    # Re-schedule: unconditionally unschedule + reschedule. Cheap and
    # avoids subtle bugs around partial schedule changes.
    rescheduled = False
    try:
        from spark.scheduler import get_scheduler  # noqa: PLC0415

        sched = get_scheduler()
        if sched is not None:
            try:
                await sched.unschedule(task_name)
            except Exception:  # pragma: no cover
                pass
            if schedule_obj is not None:
                from spark.config.loader import load_agent  # noqa: PLC0415

                agent_path = (
                    Path("~/.spark/agents").expanduser()
                    / f"{reloaded.spec.agent}.yaml"
                )
                if agent_path.exists():
                    agent_obj = load_agent(agent_path)
                    await sched.schedule_task(agent_obj, reloaded)
                    rescheduled = True
    except Exception as exc:  # pragma: no cover
        log.warning("task_update_reschedule_failed", task=task_name, error=str(exc))

    return {
        "ok": True,
        "name": task_name,
        "config_path": str(target),
        "agent_changed": agent_changed,
        "rescheduled": rescheduled,
    }


@router.post("/tasks/{task_name}/pause")
async def pause_task(
    task_name: str, _: Principal = Depends(require_operator)
) -> dict[str, bool]:
    async with session_scope() as session:
        row = await session.get(TaskRow, task_name)
        if row is None:
            raise HTTPException(status_code=404, detail="task not found")
        row.state = "paused"
    return {"ok": True}


@router.post("/tasks/{task_name}/stop")
async def stop_task(
    task_name: str, _: Principal = Depends(require_operator)
) -> dict[str, bool]:
    async with session_scope() as session:
        row = await session.get(TaskRow, task_name)
        if row is None:
            raise HTTPException(status_code=404, detail="task not found")
        row.state = "stopped"
    return {"ok": True}


@router.get("/runs", response_model=list[TaskRunSummary])
async def list_runs(
    limit: int = 100,
    state: str | None = None,
    task_name: str | None = None,
    _: Principal = Depends(require_viewer),
) -> list[TaskRunSummary]:
    async with session_scope() as session:
        stmt = select(TaskRunRow).order_by(TaskRunRow.started_at.desc()).limit(limit)
        if state is not None:
            stmt = stmt.where(TaskRunRow.state == state)
        if task_name is not None:
            stmt = stmt.where(TaskRunRow.task_name == task_name)
        result = await session.execute(stmt)
        rows = list(result.scalars().all())
    return [
        TaskRunSummary(
            run_id=r.run_id,
            task_name=r.task_name,
            agent_name=r.agent_name,
            state=r.state,
            started_at=r.started_at,
            finished_at=r.finished_at,
            iterations=r.iterations,
            model_calls=r.model_calls,
            tool_calls=r.tool_calls,
            summary=r.summary,
            error=r.error,
        )
        for r in rows
    ]


@router.get("/runs/{run_id}", response_model=TaskRunSummary)
async def get_run(
    run_id: str, _: Principal = Depends(require_viewer)
) -> TaskRunSummary:
    async with session_scope() as session:
        row = await session.get(TaskRunRow, run_id)
    if row is None:
        raise HTTPException(status_code=404, detail="run not found")
    return TaskRunSummary(
        run_id=row.run_id,
        task_name=row.task_name,
        agent_name=row.agent_name,
        state=row.state,
        started_at=row.started_at,
        finished_at=row.finished_at,
        iterations=row.iterations,
        model_calls=row.model_calls,
        tool_calls=row.tool_calls,
        summary=row.summary,
        error=row.error,
    )


@router.get("/schedules")
async def list_schedules(
    _: Principal = Depends(require_viewer),
) -> list[dict[str, object]]:
    async with session_scope() as session:
        result = await session.execute(select(ScheduleRow))
        rows = list(result.scalars().all())
    return [
        {
            "task_name": r.task_name,
            "trigger_type": r.trigger_type,
            "trigger_expression": r.trigger_expression,
            "timezone": r.timezone,
            "enabled": r.enabled,
            "next_run_at": r.next_run_at,
        }
        for r in rows
    ]


@router.post("/trigger")
async def trigger_now(
    body: TaskTriggerRequest, principal: Principal = Depends(require_operator)
) -> dict[str, str]:
    """Fire a task immediately, regardless of its schedule.

    For tasks with a cron / interval schedule, the job_runner is also wired
    to ``execute_task_by_name`` — so a triggered run and a scheduled tick
    flow through the same lifecycle. For one_shot tasks (no schedule) this
    is the only path that actually runs them.
    """
    import asyncio

    from spark.scheduler.executor import execute_task_by_name

    async with session_scope() as session:
        row = await session.get(TaskRow, body.task_name)
        if row is None:
            raise HTTPException(status_code=404, detail="task not found")
        if row.state == "running":
            raise HTTPException(
                status_code=409, detail="task is already running"
            )
        if row.state == "dlq":
            raise HTTPException(
                status_code=409, detail="task is in DLQ — ack it first"
            )
        # Reset terminal / passive states back to scheduled so the run row
        # transition shows the right lineage.
        if row.state in ("completed", "failed", "stopped", "sleeping"):
            row.state = "scheduled"

    # Fire-and-forget: we don't await the run because one_shots can take
    # minutes and we don't want to block the HTTP response. Errors are
    # logged inside execute_task_by_name.
    asyncio.create_task(
        execute_task_by_name(body.task_name, triggered_by=f"web:{principal.subject}")
    )
    return {"status": "scheduled"}


# -----------------------------------------------------------------------------
# F5 — approvals, DLQ ack, simulation, webhooks
# -----------------------------------------------------------------------------


class SimulateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schedule_type: str = Field(pattern="^(cron|interval)$")
    expression: str = Field(min_length=1, max_length=128)
    timezone: str = Field(default="UTC", min_length=1, max_length=64)
    horizon_hours: int = Field(default=168, ge=1, le=24 * 90)


@router.post("/simulate")
async def simulate_schedule(
    body: SimulateRequest, _: Principal = Depends(require_viewer)
) -> dict[str, Any]:
    """Return predicted fire times for a schedule without persisting anything."""
    from spark.config.models import CronSchedule, IntervalSchedule
    from spark.scheduler.simulate import simulate

    if body.schedule_type == "cron":
        sched = CronSchedule(expression=body.expression, timezone=body.timezone)
    else:
        sched = IntervalSchedule(seconds=int(body.expression), timezone=body.timezone)
    try:
        fires = simulate(sched, horizon_hours=body.horizon_hours)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"simulation failed: {exc}") from exc
    from spark.utils.time import isoformat as iso_utc  # noqa: PLC0415

    return {"count": len(fires), "fires": [iso_utc(dt) for dt in fires]}


@router.get("/approvals")
async def list_approvals(
    _: Principal = Depends(require_viewer),
) -> list[dict[str, Any]]:
    """List runs that are paused awaiting operator approval."""
    async with session_scope() as session:
        stmt = select(TaskRow).where(TaskRow.state == "paused")
        result = await session.execute(stmt)
        rows = list(result.scalars().all())
    return [
        {"task_name": r.name, "agent": r.agent_name, "updated_at": r.updated_at}
        for r in rows
    ]


@router.post("/approvals/{task_name}")
async def approve_task(
    task_name: str, principal: Principal = Depends(require_operator)
) -> dict[str, str]:
    async with session_scope() as session:
        row = await session.get(TaskRow, task_name)
        if row is None:
            raise HTTPException(status_code=404, detail="task not found")
        row.state = "scheduled"
        await AuditRepository(session).append(
            actor=principal.subject,
            kind="task.approved",
            target=task_name,
            severity="elevated",
        )
    return {"status": "scheduled"}


@router.post("/dlq/{task_name}/ack")
async def ack_dlq(
    task_name: str, principal: Principal = Depends(require_operator)
) -> dict[str, str]:
    """Acknowledge a DLQ'd task, resetting consecutive_failures and state."""
    async with session_scope() as session:
        row = await session.get(TaskRow, task_name)
        if row is None:
            raise HTTPException(status_code=404, detail="task not found")
        if row.state != "dlq":
            raise HTTPException(status_code=409, detail="task is not in dlq")
        row.state = "scheduled"
        # Reset the most recent run row's counter too.
        stmt = (
            select(TaskRunRow)
            .where(TaskRunRow.task_name == task_name)
            .order_by(TaskRunRow.started_at.desc())
            .limit(1)
        )
        result = await session.execute(stmt)
        latest = result.scalars().first()
        if latest is not None:
            latest.consecutive_failures = 0
        await AuditRepository(session).append(
            actor=principal.subject,
            kind="task.dlq_ack",
            target=task_name,
            severity="elevated",
        )
    return {"status": "scheduled"}


_HMAC_LOCKOUT_THRESHOLD = 10
_HMAC_LOCKOUT_MINUTES = 15

#: Hard cap on inbound webhook body size. GitHub maxes payloads at 25
#: MB; Slack at 3 KB. Most providers stay well under 5 MB. We reject
#: larger to bound the memory + CPU cost of HMAC verification.
_WEBHOOK_MAX_BODY_BYTES = 5 * 1024 * 1024


class TriggerCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    trigger_id: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9._-]+$")
    task_name: str = Field(min_length=1, max_length=128)
    rate_limit_per_hour: int = Field(default=60, ge=0, le=10_000)
    auth_mode: Literal["bearer", "hmac_sha256", "hmac_sha256_slack"] = "bearer"
    body_parser: Literal["json", "form", "raw"] = "json"
    payload_forwarding: bool = False
    event_filter: dict[str, Any] | None = None


class TriggerCreatedResponse(BaseModel):
    trigger_id: str
    task_name: str
    auth_mode: str
    # Cleartext credential — shown ONCE.
    # ``bearer``: send via ``X-Spark-Token`` header.
    # ``hmac_sha256``: configure as the webhook signing secret upstream.
    secret: str


@router.post("/triggers", response_model=TriggerCreatedResponse)
async def create_trigger(
    body: TriggerCreate, principal: Principal = Depends(require_admin)
) -> TriggerCreatedResponse:
    """Create a webhook trigger. Returns the cleartext credential exactly once."""
    import json as _json

    from spark.web.credentials import hash_password

    cleartext = _secrets.token_urlsafe(32)
    secret_name: str | None = None
    token_hash = ""

    if body.auth_mode == "bearer":
        token_hash = hash_password(cleartext)
    else:
        # All HMAC modes need the cleartext for verification — store in
        # the age vault rather than bcrypt-hashing.
        secret_name = f"webhook.trigger.{body.trigger_id}.hmac_secret"
        try:
            from spark.runtime import get_secret_manager  # noqa: PLC0415

            get_secret_manager().set(secret_name, cleartext)
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail=f"cannot store HMAC secret: {exc}",
            ) from exc

    async with session_scope() as session:
        existing = await session.get(TaskRow, body.task_name)
        if existing is None:
            raise HTTPException(status_code=404, detail="task not found")
        row = TriggerRow(
            trigger_id=body.trigger_id,
            task_name=body.task_name,
            token_hash=token_hash,
            auth_mode=body.auth_mode,
            secret_name=secret_name,
            body_parser=body.body_parser,
            payload_forwarding=body.payload_forwarding,
            event_filter_json=_json.dumps(body.event_filter) if body.event_filter else None,
            rate_limit_per_hour=body.rate_limit_per_hour,
            enabled=True,
        )
        await TriggerRepository(session).create(row)
        await AuditRepository(session).append(
            actor=principal.subject,
            kind="trigger.created",
            target=body.trigger_id,
            diff={
                "task_name": body.task_name,
                "auth_mode": body.auth_mode,
                "payload_forwarding": body.payload_forwarding,
                "event_filter": bool(body.event_filter),
            },
            severity="elevated",
        )
    return TriggerCreatedResponse(
        trigger_id=body.trigger_id,
        task_name=body.task_name,
        auth_mode=body.auth_mode,
        secret=cleartext,
    )


@router.get("/triggers")
async def list_triggers(_: Principal = Depends(require_viewer)) -> list[dict[str, Any]]:
    async with session_scope() as session:
        rows = await TriggerRepository(session).list_all()
    return [
        {
            "trigger_id": r.trigger_id,
            "task_name": r.task_name,
            "enabled": r.enabled,
            "auth_mode": r.auth_mode,
            "body_parser": r.body_parser,
            "payload_forwarding": r.payload_forwarding,
            "event_filter": r.event_filter_json,
            "rate_limit_per_hour": r.rate_limit_per_hour,
            "fires_total": r.fires_total,
            "last_fired_at": r.last_fired_at,
            "failed_verify_count": r.failed_verify_count,
            "locked_until": r.locked_until,
        }
        for r in rows
    ]


@router.delete("/triggers/{trigger_id}")
async def delete_trigger(
    trigger_id: str, principal: Principal = Depends(require_admin)
) -> dict[str, bool]:
    async with session_scope() as session:
        repo = TriggerRepository(session)
        trigger = await repo.get(trigger_id)
        secret_name = trigger.secret_name if trigger else None
        removed = await repo.delete(trigger_id)
        if removed:
            await AuditRepository(session).append(
                actor=principal.subject,
                kind="trigger.deleted",
                target=trigger_id,
                severity="elevated",
            )

    # Best-effort secret cleanup. Done outside the DB transaction so a
    # vault hiccup doesn't roll back the row delete.
    if removed and secret_name:
        try:
            from spark.runtime import get_secret_manager  # noqa: PLC0415

            get_secret_manager().delete(secret_name)
        except Exception:  # pragma: no cover — best-effort
            pass

    return {"ok": removed}


@router.post("/webhooks/{trigger_id}")
async def fire_webhook(trigger_id: str, request: Request) -> dict[str, Any]:
    """Public webhook endpoint. Authenticates the caller, optionally
    verifies an HMAC signature, optionally forwards the body to the task,
    and (if enabled) executes the task immediately.

    Auth modes:

    - ``bearer``: ``X-Spark-Token: <token>``. Constant-time verified
      against the bcrypt hash.
    - ``hmac_sha256``: ``X-Hub-Signature-256: sha256=<hex>`` (or
      ``X-Slack-Signature``, etc. — whatever the upstream sends with
      that scheme). The shared secret is read from the age vault. After
      ``_HMAC_LOCKOUT_THRESHOLD`` consecutive bad signatures the
      trigger is locked for ``_HMAC_LOCKOUT_MINUTES``; this thwarts
      credential-stuffing on a leaked endpoint URL.

    On success the inbound body (capped at the documented limit) lives
    on ``RunState.trigger_payload`` and is rendered into the planner's
    first system prompt. The full unabridged body is persisted as
    ``TaskRunRow.trigger_payload_json`` for replay.
    """
    import asyncio as _asyncio
    import json as _json
    from datetime import timedelta
    from urllib.parse import parse_qsl

    from spark.logging import EventType, get_logger
    from spark.scheduler.executor import execute_task_by_name
    from spark.utils.auth import verify_hmac_sha256, verify_hmac_sha256_slack
    from spark.utils.time import utcnow
    from spark.web.credentials import verify_password

    log = get_logger("spark.web.webhook")

    # Cheap pre-check via Content-Length when present — refuse oversize
    # bodies before allocating anything. The body() read below also
    # caps via Starlette's request size limit at the ASGI layer if
    # configured, but we belt-and-braces here for self-hosted setups.
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > _WEBHOOK_MAX_BODY_BYTES:
        raise HTTPException(status_code=413, detail="webhook body too large")

    # Read the body once — both HMAC verification and payload forwarding
    # need bytes. Verification MUST happen against the raw bytes, not the
    # parsed form; signature schemes hash the wire bytes verbatim.
    body_bytes = await request.body()
    if len(body_bytes) > _WEBHOOK_MAX_BODY_BYTES:
        raise HTTPException(status_code=413, detail="webhook body too large")

    async with session_scope() as session:
        repo = TriggerRepository(session)
        trigger = await repo.get(trigger_id)
        if trigger is None or not trigger.enabled:
            raise HTTPException(status_code=404, detail="trigger not found")

        if trigger.locked_until is not None and trigger.locked_until > utcnow():
            raise HTTPException(
                status_code=401,
                detail="trigger temporarily locked due to repeated bad signatures",
            )

        # Slack URL-verification handshake. Slack pings a freshly-created
        # subscription with ``{"type": "url_verification", "challenge":
        # "<random>"}`` and expects the challenge echoed back. The
        # signing secret isn't yet active at handshake time, so we
        # short-circuit auth — but ONLY for triggers explicitly
        # configured for Slack-style HMAC. This prevents the endpoint
        # from doubling as an open echo server for any trigger ID.
        if (
            trigger.auth_mode == "hmac_sha256_slack"
            and body_bytes.startswith(b"{")
            and len(body_bytes) < 4096
        ):
            try:
                preview = _json.loads(body_bytes.decode("utf-8"))
            except Exception:
                preview = None
            if (
                isinstance(preview, dict)
                and preview.get("type") == "url_verification"
                and isinstance(preview.get("challenge"), str)
            ):
                return {"challenge": preview["challenge"]}

        verified = False
        if trigger.auth_mode == "bearer":
            supplied = request.headers.get("x-spark-token", "")
            if supplied and verify_password(supplied, trigger.token_hash):
                verified = True
        elif trigger.auth_mode in ("hmac_sha256", "hmac_sha256_slack"):
            if not trigger.secret_name:
                raise HTTPException(
                    status_code=500,
                    detail="trigger configured for HMAC but has no secret_name",
                )
            try:
                from spark.runtime import get_secret_manager  # noqa: PLC0415

                secret = get_secret_manager().get(trigger.secret_name).get_secret_value()
            except Exception as exc:
                # Likely vault locked or secret deleted — operator-actionable.
                log.warning(
                    "webhook.vault_unavailable",
                    trigger_id=trigger_id,
                    error=str(exc),
                )
                raise HTTPException(
                    status_code=503,
                    detail="vault unavailable; cannot verify signed webhooks",
                ) from exc

            if trigger.auth_mode == "hmac_sha256_slack":
                sig = request.headers.get("x-slack-signature", "")
                ts = request.headers.get("x-slack-request-timestamp", "")
                verified = verify_hmac_sha256_slack(secret, body_bytes, sig, ts)
            else:
                sig = (
                    request.headers.get("x-hub-signature-256")
                    or request.headers.get("x-spark-signature-256")
                    or request.headers.get("x-signature-sha256")
                    or ""
                )
                verified = verify_hmac_sha256(secret, body_bytes, sig)

        if not verified:
            trigger.failed_verify_count = (trigger.failed_verify_count or 0) + 1
            if trigger.failed_verify_count >= _HMAC_LOCKOUT_THRESHOLD:
                trigger.locked_until = utcnow() + timedelta(
                    minutes=_HMAC_LOCKOUT_MINUTES
                )
                await AuditRepository(session).append(
                    actor="webhook",
                    kind="trigger.locked",
                    target=trigger_id,
                    diff={"reason": "verify_failures_exceeded"},
                    severity="elevated",
                )
            raise HTTPException(status_code=401, detail="signature verification failed")

        # Verified. Reset the bad-signature counter.
        trigger.failed_verify_count = 0

        # Rate limit: soft cap on fires within the last hour.
        if trigger.rate_limit_per_hour > 0 and trigger.last_fired_at is not None:
            since = utcnow() - timedelta(hours=1)
            if (
                trigger.last_fired_at >= since
                and trigger.fires_total >= trigger.rate_limit_per_hour
            ):
                raise HTTPException(status_code=429, detail="trigger rate limit exceeded")

        # Parse the body according to the trigger's body_parser.
        # ``json`` (default): structured JSON, walked by event_filter.
        # ``form``: application/x-www-form-urlencoded → flat dict.
        # ``raw``: passthrough as ``{"raw": "<utf8>"}`` so the agent can
        #          read it without us imposing a structure.
        payload: dict[str, Any] | None = None
        if body_bytes:
            parser = trigger.body_parser or "json"
            if parser == "json":
                try:
                    parsed = _json.loads(body_bytes.decode("utf-8"))
                    payload = parsed if isinstance(parsed, dict) else {"value": parsed}
                except Exception:
                    payload = None
            elif parser == "form":
                try:
                    payload = dict(parse_qsl(body_bytes.decode("utf-8"), keep_blank_values=True))
                except Exception:
                    payload = None
            else:  # raw
                try:
                    payload = {"raw": body_bytes.decode("utf-8", errors="replace")}
                except Exception:
                    payload = None
        if trigger.event_filter_json:
            try:
                rules = _json.loads(trigger.event_filter_json)
            except Exception:
                rules = {}
            if not _matches_event_filter(payload, rules):
                await AuditRepository(session).append(
                    actor="webhook",
                    kind="trigger.filtered",
                    target=trigger_id,
                    diff={"task_name": trigger.task_name},
                    severity="info",
                )
                return {"status": "filtered", "task_name": trigger.task_name}

        task_row = await session.get(TaskRow, trigger.task_name)
        if task_row is None:
            raise HTTPException(status_code=404, detail="trigger target task missing")
        if task_row.state in ("completed", "failed", "stopped", "sleeping"):
            task_row.state = "scheduled"

        await repo.mark_fired(trigger_id)
        await AuditRepository(session).append(
            actor="webhook",
            kind="trigger.fired",
            target=trigger_id,
            diff={
                "task_name": trigger.task_name,
                "auth_mode": trigger.auth_mode,
                "payload_forwarded": bool(trigger.payload_forwarding and payload),
            },
            severity="info",
        )
        log.info(
            "webhook.fired",
            event_type=EventType.WEBHOOK_FIRED,
            trigger_id=trigger_id,
            task_name=trigger.task_name,
        )

    forwarded_payload = payload if trigger.payload_forwarding else None
    _asyncio.create_task(
        execute_task_by_name(
            trigger.task_name,
            triggered_by=f"webhook:{trigger_id}",
            payload=forwarded_payload,
        )
    )
    return {"status": "scheduled", "task_name": trigger.task_name}


def _matches_event_filter(payload: Any, rules: dict[str, Any]) -> bool:
    """Walk dotted paths through ``payload`` and compare against rules.

    Returns ``True`` when every rule matches. Missing path components or
    non-dict intermediates count as a non-match. Used to gate
    GitHub-style webhooks (e.g. fire only on
    ``{"action": "closed", "pull_request.merged": true}``).
    """
    if not rules:
        return True
    if payload is None:
        return False
    for path, expected in rules.items():
        cursor: Any = payload
        for segment in path.split("."):
            if isinstance(cursor, dict) and segment in cursor:
                cursor = cursor[segment]
            else:
                return False
        if cursor != expected:
            return False
    return True
