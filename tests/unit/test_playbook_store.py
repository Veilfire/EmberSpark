"""End-to-end test of the PlaybookStore against a real SQLite DB."""

from __future__ import annotations

from pathlib import Path

import pytest

from spark.learning.playbooks import PlaybookCandidate, PlaybookStore
from spark.persistence.db import dispose, init_db


@pytest.fixture
async def db(tmp_path: Path):
    await init_db(tmp_path / "spark.db")
    yield
    await dispose()


@pytest.mark.asyncio
async def test_upsert_and_select(db):
    store = PlaybookStore()
    candidate = PlaybookCandidate(
        name="summarize-repo",
        description="summarize a repo README",
        objective_hint="summarize the repo README and extract decisions",
        tool_sequence=["http_client", "filesystem"],
    )
    pb = await store.upsert_from_candidate(agent_name="alpha", candidate=candidate)
    assert pb.uses == 0
    assert pb.name == "summarize-repo"

    # Idempotent upsert via fingerprint match.
    pb2 = await store.upsert_from_candidate(agent_name="alpha", candidate=candidate)
    assert pb2.playbook_id == pb.playbook_id


@pytest.mark.asyncio
async def test_find_applicable_filters_by_available_tools(db):
    store = PlaybookStore()
    await store.upsert_from_candidate(
        agent_name="alpha",
        candidate=PlaybookCandidate(
            name="net-task",
            description="needs network",
            objective_hint="call api for status",
            tool_sequence=["http_client"],
        ),
    )
    # Only available tool is filesystem — playbook should not be applicable.
    apps = await store.find_applicable(
        agent_name="alpha",
        objective="call api for status",
        available_tools={"filesystem"},
    )
    assert apps == []

    apps2 = await store.find_applicable(
        agent_name="alpha",
        objective="call api for status",
        available_tools={"http_client"},
    )
    assert len(apps2) == 1


@pytest.mark.asyncio
async def test_record_outcome_updates_bandit_state(db):
    store = PlaybookStore()
    pb = await store.upsert_from_candidate(
        agent_name="alpha",
        candidate=PlaybookCandidate(
            name="x",
            description="x",
            objective_hint="x",
            tool_sequence=["a"],
        ),
    )
    await store.record_outcome(
        playbook_id=pb.playbook_id,
        run_id="r1",
        success=True,
        duration_seconds=1.5,
        tool_calls=2,
        model_calls=3,
    )
    reloaded = (await store.list_for_agent("alpha"))[0]
    assert reloaded.uses == 1
    assert reloaded.alpha > 1.0
    assert reloaded.avg_duration_seconds > 0
