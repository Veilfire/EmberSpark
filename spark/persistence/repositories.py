"""Thin repository layer. All queries are bound — no string building."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from spark.persistence.models import (
    AgentRow,
    LongTermMemoryIndexRow,
    PluginRegistryRow,
    ReflectionRow,
    ScheduleRow,
    SessionMemoryRow,
    SessionRow,
    TaskRow,
    TaskRunRow,
)


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


class TaskRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def upsert(self, row: TaskRow) -> None:
        existing = await self.session.get(TaskRow, row.name)
        if existing is None:
            self.session.add(row)
        else:
            existing.agent_name = row.agent_name
            existing.mode = row.mode
            existing.config_hash = row.config_hash
            existing.config_path = row.config_path
            existing.state = row.state
            existing.updated_at = _now()

    async def set_state(self, name: str, state: str) -> None:
        row = await self.session.get(TaskRow, name)
        if row is None:
            return
        row.state = state
        row.updated_at = _now()

    async def get(self, name: str) -> TaskRow | None:
        return await self.session.get(TaskRow, name)

    async def list_all(self) -> list[TaskRow]:
        result = await self.session.execute(select(TaskRow))
        return list(result.scalars().all())


class TaskRunRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, row: TaskRunRow) -> None:
        self.session.add(row)

    async def finish(
        self,
        run_id: str,
        *,
        state: str,
        error: str | None = None,
        summary: str | None = None,
        iterations: int | None = None,
        model_calls: int | None = None,
        tool_calls: int | None = None,
        result_text: str | None = None,
    ) -> None:
        row = await self.session.get(TaskRunRow, run_id)
        if row is None:
            return
        row.state = state
        row.error = error
        row.summary = summary
        row.finished_at = _now()
        if iterations is not None:
            row.iterations = iterations
        if model_calls is not None:
            row.model_calls = model_calls
        if tool_calls is not None:
            row.tool_calls = tool_calls
        if result_text is not None:
            row.result_text = result_text

    async def reconcile_orphans(self, alive_run_ids: set[str]) -> int:
        """Mark as failed any run still in a live state that isn't alive."""
        stmt = select(TaskRunRow).where(TaskRunRow.state.in_(("running", "scheduled", "sleeping")))
        result = await self.session.execute(stmt)
        count = 0
        for row in result.scalars().all():
            if row.run_id not in alive_run_ids:
                row.state = "failed"
                row.error = "orphaned by process restart"
                row.finished_at = _now()
                count += 1
        return count


class AgentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def upsert(self, row: AgentRow) -> None:
        existing = await self.session.get(AgentRow, row.name)
        if existing is None:
            self.session.add(row)
        else:
            existing.description = row.description
            existing.config_hash = row.config_hash
            existing.updated_at = _now()


class ScheduleRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def upsert(self, row: ScheduleRow) -> None:
        existing = await self.session.get(ScheduleRow, row.task_name)
        if existing is None:
            self.session.add(row)
        else:
            existing.trigger_type = row.trigger_type
            existing.trigger_expression = row.trigger_expression
            existing.timezone = row.timezone
            existing.next_run_at = row.next_run_at
            existing.enabled = row.enabled

    async def list_all(self) -> list[ScheduleRow]:
        result = await self.session.execute(select(ScheduleRow))
        return list(result.scalars().all())

    async def delete(self, task_name: str) -> None:
        row = await self.session.get(ScheduleRow, task_name)
        if row is not None:
            await self.session.delete(row)


class PluginRegistryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def record(
        self, *, name: str, version: str, module_hash: str
    ) -> tuple[bool, str | None]:
        """Record a plugin load; returns (is_new, previous_hash_if_changed)."""
        existing = await self.session.get(PluginRegistryRow, name)
        if existing is None:
            self.session.add(
                PluginRegistryRow(name=name, version=version, module_hash=module_hash)
            )
            return True, None
        previous = existing.module_hash if existing.module_hash != module_hash else None
        existing.version = version
        existing.module_hash = module_hash
        existing.last_seen_at = _now()
        return False, previous


class ReflectionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, row: ReflectionRow) -> None:
        self.session.add(row)


class LongTermMemoryIndexRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def upsert(self, row: LongTermMemoryIndexRow) -> None:
        existing = await self.session.get(LongTermMemoryIndexRow, row.memory_id)
        if existing is None:
            self.session.add(row)
        else:
            existing.memory_type = row.memory_type
            existing.source_type = row.source_type
            existing.sensitivity = row.sensitivity
            existing.retention_class = row.retention_class
            existing.confidence = row.confidence
            existing.content_summary = row.content_summary
            existing.canonical_hash = row.canonical_hash
            existing.tags = row.tags
            existing.updated_at = _now()

    async def find_by_hash(
        self, namespace: str, canonical_hash: str
    ) -> LongTermMemoryIndexRow | None:
        stmt = select(LongTermMemoryIndexRow).where(
            (LongTermMemoryIndexRow.namespace == namespace)
            & (LongTermMemoryIndexRow.canonical_hash == canonical_hash)
        )
        result = await self.session.execute(stmt)
        return result.scalars().first()


class SessionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def ensure(self, *, session_id: str, name: str, agent_name: str) -> None:
        existing = await self.session.get(SessionRow, session_id)
        if existing is None:
            self.session.add(
                SessionRow(session_id=session_id, name=name, agent_name=agent_name)
            )

    async def append_memory(self, row: SessionMemoryRow) -> None:
        self.session.add(row)

    async def list_memory(
        self, session_id: str, limit: int
    ) -> list[SessionMemoryRow]:
        stmt = (
            select(SessionMemoryRow)
            .where(SessionMemoryRow.session_id == session_id)
            .order_by(SessionMemoryRow.id.desc())
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def prune(self, session_id: str, max_entries: int) -> int:
        stmt = (
            select(SessionMemoryRow.id)
            .where(SessionMemoryRow.session_id == session_id)
            .order_by(SessionMemoryRow.id.desc())
            .offset(max_entries)
        )
        result = await self.session.execute(stmt)
        stale_ids = [r for r in result.scalars().all()]
        if not stale_ids:
            return 0
        for stale in stale_ids:
            row = await self.session.get(SessionMemoryRow, stale)
            if row is not None:
                await self.session.delete(row)
        return len(stale_ids)
