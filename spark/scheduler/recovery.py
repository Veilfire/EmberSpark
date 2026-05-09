"""Startup reconciliation helpers — marks orphaned runs failed."""

from __future__ import annotations

from spark.persistence.db import session_scope
from spark.persistence.repositories import TaskRunRepository


async def reconcile_orphaned_runs() -> int:
    async with session_scope() as session:
        repo = TaskRunRepository(session)
        return await repo.reconcile_orphans(alive_run_ids=set())
