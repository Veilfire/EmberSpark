"""Integration test: run a full engine loop against the stub chat model.

This exercises:
- config loading
- DB init + task run row creation
- plugin registry + tool executor seam
- stub chat model → tool-call JSON parsing
- reflection short-circuit (model returns success=False so no memory promotion)

It does *not* spawn a real sandboxed subprocess — we patch
`run_sandboxed` to return a canned ResponseFrame. A separate sandbox-gated
test validates the real bubblewrap/seatbelt path.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import BaseModel

from spark.config.enums import Permission, Sensitivity
from spark.config.loader import load_agent, load_task
from spark.persistence.db import dispose, init_db
from spark.plugins.registry import PluginRegistry
from spark.providers.stub import StubChatModel
from spark.runtime.lifecycle import Lifecycle
from spark.sandbox.ipc import ResponseFrame
from spark.secrets import SecretManager
from spark.secrets.env_backend import EnvBackend

AGENT_YAML = """
apiVersion: spark.veilfire.dev/v1alpha1
kind: Agent
metadata:
  name: e2e-agent
spec:
  description: integration test agent
  runtime:
    provider:
      type: openai
      model: gpt-4.1
      api_key_ref: openai_key
    max_iterations: 4
    max_model_calls: 6
    max_tool_calls: 6
    reflection: false
  plugins:
    allow:
      - echo
"""

TASK_YAML = """
apiVersion: spark.veilfire.dev/v1alpha1
kind: Task
metadata:
  name: e2e-task
spec:
  agent: e2e-agent
  mode: one_shot
  objective: say hello via the echo tool
"""


class _EchoArgs(BaseModel):
    text: str


class _EchoResult(BaseModel):
    text: str


class EchoPlugin:
    name: ClassVar[str] = "echo"
    version: ClassVar[str] = "0.1.0"
    description: ClassVar[str] = "echo"
    input_schema: ClassVar[type[BaseModel]] = _EchoArgs
    output_schema: ClassVar[type[BaseModel]] = _EchoResult
    required_permissions: ClassVar[frozenset[Permission]] = frozenset()
    required_secrets: ClassVar[frozenset[str]] = frozenset()
    sensitivity: ClassVar[Sensitivity] = Sensitivity.LOW
    filter_output_before_model: ClassVar[bool] = True
    needs_network: ClassVar[bool] = False

    async def execute(self, args: _EchoArgs, ctx: Any) -> _EchoResult:  # pragma: no cover
        return _EchoResult(text=args.text)


@pytest.mark.asyncio
async def test_end_to_end_engine_run(tmp_path: Path):
    agent_path = tmp_path / "agent.yaml"
    task_path = tmp_path / "task.yaml"
    agent_path.write_text(AGENT_YAML)
    task_path.write_text(TASK_YAML)

    agent = load_agent(agent_path)
    task = load_task(task_path)

    await init_db(tmp_path / "spark.db")
    try:
        registry = PluginRegistry()
        registry.register_class(EchoPlugin)
        secrets = SecretManager([EnvBackend(silence_warning=True)])

        script = [
            {"content": '{"tool": "echo", "args": {"text": "hello world"}}'},
            {"content": "final answer: hello world"},
        ]
        stub = StubChatModel(script=script)

        fake_response = ResponseFrame(ok=True, result={"text": "hello world"})
        with patch(
            "spark.plugins.tool_runtime.run_sandboxed",
            AsyncMock(return_value=fake_response),
        ):
            lifecycle = Lifecycle(secrets=secrets, registry=registry)
            await lifecycle.register(agent, task)
            result = await lifecycle.run_once(agent, task, chat_model=stub)

        assert result.state.value == "completed"
        assert result.tool_calls == 1
        assert result.model_calls >= 2
    finally:
        await dispose()
