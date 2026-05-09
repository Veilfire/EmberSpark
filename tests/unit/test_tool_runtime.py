"""Tests for the ToolExecutor seam — allowlist, permissions, validation."""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import BaseModel

from spark.config.enums import Permission, Sensitivity
from spark.config.loader import load_agent
from spark.plugins.base import PermissionDenied
from spark.plugins.registry import PluginRegistry
from spark.plugins.tool_runtime import BudgetGuard, ToolExecutor
from spark.sandbox.ipc import ResponseFrame
from spark.secrets import SecretManager
from spark.secrets.env_backend import EnvBackend


class _Args(BaseModel):
    echo: str


class _Out(BaseModel):
    echoed: str


class _EchoPlugin:
    name: ClassVar[str] = "echo"
    version: ClassVar[str] = "0.1.0"
    description: ClassVar[str] = "echo plugin"
    input_schema: ClassVar[type[BaseModel]] = _Args
    output_schema: ClassVar[type[BaseModel]] = _Out
    required_permissions: ClassVar[frozenset[Permission]] = frozenset()
    required_secrets: ClassVar[frozenset[str]] = frozenset()
    sensitivity: ClassVar[Sensitivity] = Sensitivity.LOW
    filter_output_before_model: ClassVar[bool] = True
    needs_network: ClassVar[bool] = False

    async def execute(self, args: _Args, ctx: Any) -> _Out:  # pragma: no cover
        return _Out(echoed=args.echo)


AGENT_YAML = """
apiVersion: spark.veilfire.dev/v1alpha1
kind: Agent
metadata:
  name: toolrt-test
spec:
  description: test
  runtime:
    provider:
      type: openai
      model: gpt-4.1
      api_key_ref: openai_key
    max_iterations: 4
    max_model_calls: 4
    max_tool_calls: 4
  plugins:
    allow:
      - echo
  permissions:
    sandbox:
      enabled: true
"""


@pytest.fixture
def agent(tmp_path: Path):
    p = tmp_path / "agent.yaml"
    p.write_text(AGENT_YAML)
    return load_agent(p)


@pytest.fixture
def registry() -> PluginRegistry:
    reg = PluginRegistry()
    reg.register_class(_EchoPlugin)
    return reg


@pytest.fixture
def secrets() -> SecretManager:
    return SecretManager([EnvBackend(silence_warning=True)])


@pytest.mark.asyncio
async def test_denies_plugin_not_in_allowlist(agent, registry, secrets):
    budget = BudgetGuard(max_tool_calls=5, max_model_calls=5, max_iterations=5)
    executor = ToolExecutor(
        registry=registry, secrets=secrets, agent_spec=agent.spec, budget=budget
    )
    with pytest.raises(PermissionDenied, match="allowlist"):
        await executor.call("filesystem", {})


@pytest.mark.asyncio
async def test_allowed_plugin_runs(agent, registry, secrets):
    budget = BudgetGuard(max_tool_calls=5, max_model_calls=5, max_iterations=5)
    executor = ToolExecutor(
        registry=registry, secrets=secrets, agent_spec=agent.spec, budget=budget
    )
    fake_response = ResponseFrame(ok=True, result={"echoed": "hello"})
    with patch(
        "spark.plugins.tool_runtime.run_sandboxed",
        AsyncMock(return_value=fake_response),
    ):
        outcome = await executor.call("echo", {"echo": "hello"})
    assert outcome.plugin == "echo"
    assert outcome.raw_result == {"echoed": "hello"}


@pytest.mark.asyncio
async def test_invalid_args_refused(agent, registry, secrets):
    budget = BudgetGuard(max_tool_calls=5, max_model_calls=5, max_iterations=5)
    executor = ToolExecutor(
        registry=registry, secrets=secrets, agent_spec=agent.spec, budget=budget
    )
    with pytest.raises(PermissionDenied, match="invalid args"):
        await executor.call("echo", {"wrong_key": 1})
