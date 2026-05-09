"""Task lifecycle manager — state machine + restart recovery."""

from __future__ import annotations

from dataclasses import dataclass

from spark.config.enums import TaskState
from spark.config.models import Agent, Task
from spark.logging import EventType, get_logger
from spark.persistence.db import session_scope
from spark.persistence.models import AgentRow, TaskRow
from spark.persistence.repositories import (
    AgentRepository,
    TaskRepository,
    TaskRunRepository,
)
from spark.plugins.registry import PluginRegistry
from spark.providers import build_chat_model
from spark.runtime.engine import EngineResult, RuntimeEngine
from spark.secrets import SecretManager
from spark.utils.hashing import sha256_text

log = get_logger("spark.lifecycle")

_ALLOWED_TRANSITIONS: dict[TaskState, set[TaskState]] = {
    TaskState.CREATED: {TaskState.SCHEDULED, TaskState.RUNNING, TaskState.STOPPED},
    TaskState.SCHEDULED: {TaskState.RUNNING, TaskState.STOPPED, TaskState.PAUSED},
    TaskState.RUNNING: {
        TaskState.COMPLETED,
        TaskState.FAILED,
        TaskState.SLEEPING,
        TaskState.PAUSED,
        TaskState.STOPPED,
    },
    TaskState.PAUSED: {TaskState.RUNNING, TaskState.STOPPED},
    TaskState.SLEEPING: {TaskState.RUNNING, TaskState.STOPPED},
    TaskState.COMPLETED: set(),
    TaskState.FAILED: {TaskState.SCHEDULED, TaskState.RUNNING},
    TaskState.STOPPED: {TaskState.SCHEDULED, TaskState.RUNNING},
}


class InvalidTransition(ValueError):
    pass


def assert_transition(current: TaskState, target: TaskState) -> None:
    allowed = _ALLOWED_TRANSITIONS.get(current, set())
    if target not in allowed:
        raise InvalidTransition(f"{current.value} → {target.value}")


@dataclass
class Lifecycle:
    secrets: SecretManager
    registry: PluginRegistry

    async def register(self, agent: Agent, task: Task, *, config_path: str | None = None) -> None:
        """Persist an Agent + Task so they survive restart."""
        async with session_scope() as session:
            agent_repo = AgentRepository(session)
            await agent_repo.upsert(
                AgentRow(
                    name=agent.metadata.name,
                    description=agent.spec.description,
                    config_hash=sha256_text(agent.model_dump_json()),
                )
            )
            task_repo = TaskRepository(session)
            await task_repo.upsert(
                TaskRow(
                    name=task.metadata.name,
                    agent_name=agent.metadata.name,
                    mode=task.spec.mode.value,
                    config_hash=sha256_text(task.model_dump_json()),
                    config_path=config_path,
                    state=TaskState.CREATED.value,
                )
            )

    async def run_once(
        self,
        agent: Agent,
        task: Task,
        *,
        chat_model: object | None = None,
        triggered_by: str | None = None,
        payload: dict[str, object] | None = None,
        chain: list[str] | None = None,
    ) -> EngineResult:
        # Gate on run-window constraint if present.
        if task.spec.only_between:
            from spark.scheduler.simulate import in_window
            from datetime import datetime, timezone as _tz

            if not in_window(datetime.now(tz=_tz.utc), task.spec.only_between):
                log.info(
                    "task.outside_run_window",
                    task=task.metadata.name,
                    window=task.spec.only_between,
                )
                raise PermissionError(
                    f"task {task.metadata.name!r} is outside its run window"
                )

        # Approval gate.
        if task.spec.approval.required:
            log.info(
                "task.approval_requested",
                event_type=EventType.TASK_APPROVAL_REQUESTED,
                task=task.metadata.name,
            )
            await self._set_task_state(task.metadata.name, TaskState.PAUSED)
            # Fire a HITL notification so the operator sees the pending
            # approval in the bell badge.
            from spark.notifications import NotificationKind, get_notification_service

            await get_notification_service().notify(
                NotificationKind.HITL_APPROVAL,
                title=f"Approval required: {task.metadata.name}",
                body=task.spec.approval.note or "Operator approval required before this task can run.",
                severity="elevated",
                target_kind="task",
                target_id=task.metadata.name,
                action_url=f"/scheduler?focus={task.metadata.name}",
            )
            raise PermissionError(
                f"task {task.metadata.name!r} requires operator approval"
            )

        if chat_model is None:
            try:
                chat_model = build_chat_model(agent.spec.runtime.provider, self.secrets)
            except Exception as exc:
                log.warning("provider build failed", error=str(exc))
                chat_model = None

        engine = RuntimeEngine(
            agent=agent,
            task=task,
            secrets=self.secrets,
            plugin_registry=self.registry,
            chat_model=chat_model,
            trigger_payload=payload,
        )
        await self._set_task_state(task.metadata.name, TaskState.RUNNING)
        result = await engine.run()
        await self._set_task_state(task.metadata.name, result.state)

        # Persist trigger metadata (parent / chain + raw payload) to the run row.
        if triggered_by is not None or payload is not None:
            import json as _json

            async with session_scope() as session:
                row = await session.get(TaskRunRow, result.run_id)
                if row is not None:
                    if triggered_by is not None:
                        row.triggered_by = triggered_by[:256]
                    if payload is not None:
                        try:
                            row.trigger_payload_json = _json.dumps(payload, default=str)
                        except Exception:  # pragma: no cover — best-effort
                            row.trigger_payload_json = None

        # DLQ: increment consecutive_failures on failure; reset on success.
        await self._update_consecutive_failures(
            task.metadata.name, success=result.state == TaskState.COMPLETED
        )

        # Chain dispatch: on_success / on_failure fire successor tasks.
        successor = (
            task.spec.on_success
            if result.state == TaskState.COMPLETED
            else task.spec.on_failure
        )
        if successor:
            import asyncio as _asyncio  # noqa: PLC0415

            from spark.scheduler.executor import (  # noqa: PLC0415
                execute_task_by_name,
            )

            log.info(
                "task.chain_dispatch",
                event_type=EventType.TASK_RESCHEDULED,
                parent=task.metadata.name,
                next_task=successor,
                state=result.state.value,
            )
            _asyncio.create_task(
                execute_task_by_name(
                    successor,
                    triggered_by=f"task:{task.metadata.name}",
                    triggered_by_chain=chain or [task.metadata.name],
                )
            )

        return result

    async def _update_consecutive_failures(self, task_name: str, *, success: bool) -> None:
        """Tracks consecutive failures on the task row's latest run for DLQ."""
        from sqlalchemy import select as _select

        async with session_scope() as session:
            stmt = (
                _select(TaskRunRow)
                .where(TaskRunRow.task_name == task_name)
                .order_by(TaskRunRow.started_at.desc())
                .limit(1)
            )
            result = await session.execute(stmt)
            row = result.scalars().first()
            if row is None:
                return
            if success:
                row.consecutive_failures = 0
            else:
                row.consecutive_failures = (row.consecutive_failures or 0) + 1
                if row.consecutive_failures >= 3:
                    task_repo = TaskRepository(session)
                    await task_repo.set_state(task_name, "dlq")
                    log.warning(
                        "task.dlq",
                        event_type=EventType.TASK_DLQ,
                        task=task_name,
                        consecutive_failures=row.consecutive_failures,
                    )
                    # Fire a HITL notification: the task is now dead-lettered
                    # and needs an operator to ack before it fires again.
                    from spark.notifications import (
                        NotificationKind,
                        get_notification_service,
                    )

                    await get_notification_service().notify(
                        NotificationKind.HITL_DLQ,
                        title=f"Task moved to DLQ: {task_name}",
                        body=(
                            f"Task failed {row.consecutive_failures} times in a row. "
                            "It will not fire again until you ack it."
                        ),
                        severity="elevated",
                        target_kind="task",
                        target_id=task_name,
                        action_url=f"/scheduler?focus={task_name}&tab=dlq",
                    )

    async def _set_task_state(self, task_name: str, target: TaskState) -> None:
        async with session_scope() as session:
            task_repo = TaskRepository(session)
            await task_repo.set_state(task_name, target.value)

    async def reconcile_on_startup(self) -> int:
        """Mark orphaned runs failed, return count."""
        async with session_scope() as session:
            runs = TaskRunRepository(session)
            count = await runs.reconcile_orphans(alive_run_ids=set())
        log.info("lifecycle.recovered", event_type=EventType.TASK_RESCHEDULED, orphans=count)
        return count
