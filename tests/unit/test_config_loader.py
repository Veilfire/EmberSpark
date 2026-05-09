"""Tests for YAML loading and agent/task validation."""

from __future__ import annotations

from pathlib import Path

import pytest

from spark.config.loader import ConfigLoadError, load_agent, load_task
from spark.config.validator import validate_agent, validate_task


AGENT_YAML = """
apiVersion: spark.veilfire.dev/v1alpha1
kind: Agent
metadata:
  name: test-agent
spec:
  description: test
  runtime:
    provider:
      type: openai
      model: gpt-4.1
      api_key_ref: openai_key
    max_iterations: 6
    max_model_calls: 10
    max_tool_calls: 10
    privacy_mode: strict
  plugins:
    allow:
      - filesystem
      - http_client
  permissions:
    filesystem:
      allow_paths:
        - /tmp/spark-sandbox
    network:
      allow_hosts:
        - api.github.com
    grants:
      - fs.read
      - fs.write
      - net.http
      - secrets.read
"""

TASK_YAML = """
apiVersion: spark.veilfire.dev/v1alpha1
kind: Task
metadata:
  name: test-task
spec:
  agent: test-agent
  mode: one_shot
  objective: Do something
"""


def _write(tmp_path: Path, name: str, content: str) -> Path:
    p = tmp_path / name
    p.write_text(content)
    return p


def test_agent_roundtrip(tmp_path: Path) -> None:
    agent = load_agent(_write(tmp_path, "agent.yaml", AGENT_YAML))
    assert agent.metadata.name == "test-agent"
    assert agent.spec.runtime.privacy_mode.value == "strict"
    assert "filesystem" in agent.spec.plugins.allow


def test_task_roundtrip(tmp_path: Path) -> None:
    task = load_task(_write(tmp_path, "task.yaml", TASK_YAML))
    assert task.metadata.name == "test-task"
    assert task.spec.mode.value == "one_shot"


def test_task_recurring_without_schedule_warns(tmp_path: Path) -> None:
    yaml = TASK_YAML.replace("one_shot", "recurring")
    task = load_task(_write(tmp_path, "task.yaml", yaml))
    issues = validate_task(task)
    assert any(i.code == "schedule.required" for i in issues)


def test_agent_filesystem_without_paths_warns(tmp_path: Path) -> None:
    agent = load_agent(_write(tmp_path, "agent.yaml", AGENT_YAML))
    # Poke it via a fresh YAML that omits allow_paths — we re-parse.
    bad = AGENT_YAML.replace(
        "    allow_paths:\n        - /tmp/spark-sandbox\n", "    allow_paths: []\n"
    )
    agent = load_agent(_write(tmp_path, "bad.yaml", bad))
    issues = validate_agent(agent)
    assert any(i.code == "filesystem.no_paths" for i in issues)


def test_unknown_field_rejected(tmp_path: Path) -> None:
    bad = AGENT_YAML + "  bogus_field: 1\n"
    with pytest.raises(ConfigLoadError):
        load_agent(_write(tmp_path, "agent.yaml", bad))


def test_unknown_plugin_in_provider_rejected(tmp_path: Path) -> None:
    bad = AGENT_YAML.replace("type: openai", "type: totally_fake")
    with pytest.raises(ConfigLoadError):
        load_agent(_write(tmp_path, "agent.yaml", bad))
