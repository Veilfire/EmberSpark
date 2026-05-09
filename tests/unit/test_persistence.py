"""Tests for the SQLModel persistence layer."""

from __future__ import annotations

from pathlib import Path

import pytest

from spark.persistence.db import dispose, init_db, session_scope
from spark.persistence.models import AgentRow, TaskRow, TaskRunRow
from spark.persistence.repositories import (
    AgentRepository,
    TaskRepository,
    TaskRunRepository,
)


@pytest.fixture
async def db(tmp_path: Path):
    await init_db(tmp_path / "spark.db")
    yield
    await dispose()


@pytest.mark.asyncio
async def test_upsert_agent_and_task(db):
    async with session_scope() as session:
        agents = AgentRepository(session)
        await agents.upsert(
            AgentRow(name="alpha", description="a", config_hash="abc")
        )
        tasks = TaskRepository(session)
        await tasks.upsert(
            TaskRow(
                name="alpha-task",
                agent_name="alpha",
                mode="one_shot",
                config_hash="xyz",
                state="created",
            )
        )
    async with session_scope() as session:
        rows = await TaskRepository(session).list_all()
    assert len(rows) == 1
    assert rows[0].name == "alpha-task"


@pytest.mark.asyncio
async def test_reconcile_orphans(db):
    async with session_scope() as session:
        agents = AgentRepository(session)
        await agents.upsert(AgentRow(name="a", config_hash="h"))
        runs = TaskRunRepository(session)
        await runs.create(
            TaskRunRow(
                run_id="r1", task_name="t", agent_name="a", state="running"
            )
        )
    async with session_scope() as session:
        runs = TaskRunRepository(session)
        count = await runs.reconcile_orphans(alive_run_ids=set())
    assert count == 1
