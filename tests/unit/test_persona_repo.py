"""Tests for the persona repository invariants."""

from __future__ import annotations

from pathlib import Path

import pytest

from spark.persistence.db import dispose, init_db, session_scope
from spark.persistence.learning_models import PersonaRow
from spark.persistence.learning_repos import PersonaRepository


@pytest.fixture
async def db(tmp_path: Path):
    await init_db(tmp_path / "spark.db")
    yield
    await dispose()


async def _insert(session, pid: str, active: bool = False) -> PersonaRow:
    row = PersonaRow(
        persona_id=pid,
        name=pid,
        description="",
        system_prompt="you are",
        is_active=active,
    )
    await PersonaRepository(session).upsert(row)
    return row


@pytest.mark.asyncio
async def test_activate_toggles_exactly_one(db) -> None:
    async with session_scope() as session:
        await _insert(session, "a", active=True)
        await _insert(session, "b")
        await _insert(session, "c")

    async with session_scope() as session:
        repo = PersonaRepository(session)
        await repo.activate("b")

    async with session_scope() as session:
        repo = PersonaRepository(session)
        all_rows = await repo.list_all()
        active = [r for r in all_rows if r.is_active]
    assert len(active) == 1
    assert active[0].persona_id == "b"


@pytest.mark.asyncio
async def test_activate_missing_returns_none(db) -> None:
    async with session_scope() as session:
        repo = PersonaRepository(session)
        result = await repo.activate("nope")
    assert result is None


@pytest.mark.asyncio
async def test_delete_active_refused(db) -> None:
    async with session_scope() as session:
        await _insert(session, "a", active=True)
    async with session_scope() as session:
        repo = PersonaRepository(session)
        with pytest.raises(ValueError, match="active"):
            await repo.delete("a")


@pytest.mark.asyncio
async def test_get_active_returns_row(db) -> None:
    async with session_scope() as session:
        await _insert(session, "a", active=True)
        await _insert(session, "b")
    async with session_scope() as session:
        repo = PersonaRepository(session)
        active = await repo.get_active()
    assert active is not None
    assert active.persona_id == "a"
