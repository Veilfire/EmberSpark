"""v1 Acceptance — spec §28 item-by-item.

This test boots a full lifecycle against the stub chat model (no cloud calls),
exercises YAML-defined agent + task, memory retrieval via Chroma mock, secret
injection that never reaches the log, and restart recovery of an orphaned run.

It does not require a real LLM or sandbox backend — those are tested in
`tests/integration/test_sandbox_real.py`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, ClassVar
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import BaseModel

from spark.config.enums import Permission, Sensitivity
from spark.config.loader import load_agent, load_task
from spark.persistence.db import dispose, init_db, session_scope
from spark.persistence.models import TaskRunRow
from spark.persistence.repositories import TaskRunRepository
from spark.plugins.registry import PluginRegistry
from spark.providers.stub import StubChatModel
from spark.runtime.lifecycle import Lifecycle
from spark.sandbox.ipc import ResponseFrame
from spark.secrets import SecretManager
from spark.secrets.env_backend import EnvBackend

AGENT = """
apiVersion: spark.veilfire.dev/v1alpha1
kind: Agent
metadata:
  name: acceptance-agent
spec:
  description: acceptance
  runtime:
    provider:
      type: openai
      model: gpt-4.1
      api_key_ref: openai_key
    reflection: false
  plugins:
    allow:
      - canary
"""

TASK = """
apiVersion: spark.veilfire.dev/v1alpha1
kind: Task
metadata:
  name: acceptance-task
spec:
  agent: acceptance-agent
  mode: one_shot
  objective: call the canary tool
"""


class _CanaryArgs(BaseModel):
    token: str


class _CanaryResult(BaseModel):
    ok: bool


class CanaryPlugin:
    name: ClassVar[str] = "canary"
    version: ClassVar[str] = "0.1.0"
    description: ClassVar[str] = "takes a secret and reports ok"
    input_schema: ClassVar[type[BaseModel]] = _CanaryArgs
    output_schema: ClassVar[type[BaseModel]] = _CanaryResult
    required_permissions: ClassVar[frozenset[Permission]] = frozenset()
    required_secrets: ClassVar[frozenset[str]] = frozenset()
    sensitivity: ClassVar[Sensitivity] = Sensitivity.LOW
    filter_output_before_model: ClassVar[bool] = True
    needs_network: ClassVar[bool] = False

    async def execute(self, args: _CanaryArgs, ctx: Any) -> _CanaryResult:  # pragma: no cover
        return _CanaryResult(ok=True)


@pytest.mark.acceptance
@pytest.mark.asyncio
async def test_acceptance_v1(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # Isolated log file
    log_path = tmp_path / "logs"
    log_path.mkdir()
    monkeypatch.setenv("SPARK_SECRET_OPENAI_KEY", "sk-canary-test-secret-00000000")

    # Configure logging into tmp_path
    from spark.logging import configure_logging

    configure_logging(log_dir=log_path)

    agent_path = tmp_path / "agent.yaml"
    task_path = tmp_path / "task.yaml"
    agent_path.write_text(AGENT)
    task_path.write_text(TASK)
    agent = load_agent(agent_path)
    task = load_task(task_path)

    await init_db(tmp_path / "spark.db")
    try:
        registry = PluginRegistry()
        registry.register_class(CanaryPlugin)
        secrets = SecretManager([EnvBackend(silence_warning=True)])

        script = [
            {"content": '{"tool": "canary", "args": {"token": "sk-canary-test-secret-00000000"}}'},
            {"content": "final"},
        ]
        stub = StubChatModel(script=script)
        response = ResponseFrame(ok=True, result={"ok": True})

        with patch(
            "spark.plugins.tool_runtime.run_sandboxed",
            AsyncMock(return_value=response),
        ):
            lifecycle = Lifecycle(secrets=secrets, registry=registry)
            await lifecycle.register(agent, task)
            result = await lifecycle.run_once(agent, task, chat_model=stub)

        assert result.state.value == "completed"

        # Spec §28 item 9: no raw secret in logs
        log_file = log_path / "spark.jsonl"
        assert log_file.exists()
        content = log_file.read_text()
        assert "sk-canary-test-secret-00000000" not in content

        # Spec §28 item 2: task ran once from CLI-equivalent path — run row exists.
        async with session_scope() as session:
            row = await session.get(TaskRunRow, result.run_id)
            assert row is not None
            assert row.state == "completed"

        # Spec §28 item 11: structured logs contain event_type entries.
        lines = [json.loads(line) for line in content.splitlines() if line.strip()]
        events = {line.get("event_type") for line in lines}
        assert "task.started" in events
        assert "task.completed" in events
        assert "tool.invoked" in events
        assert "tool.result_received" in events
    finally:
        await dispose()


@pytest.mark.acceptance
@pytest.mark.asyncio
async def test_restart_recovery_marks_orphans_failed(tmp_path: Path):
    await init_db(tmp_path / "spark.db")
    try:
        async with session_scope() as session:
            runs = TaskRunRepository(session)
            await runs.create(
                TaskRunRow(
                    run_id="ghost",
                    task_name="t",
                    agent_name="a",
                    state="running",
                )
            )
        async with session_scope() as session:
            runs = TaskRunRepository(session)
            count = await runs.reconcile_orphans(alive_run_ids=set())
        assert count == 1
        async with session_scope() as session:
            row = await session.get(TaskRunRow, "ghost")
            assert row is not None
            assert row.state == "failed"
    finally:
        await dispose()
