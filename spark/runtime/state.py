"""Run state — carried through the LangGraph execution and persisted for resume.

Never contains raw secrets. Plugin outputs are held in their filtered form.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class RunState:
    run_id: str
    task_name: str
    agent_name: str
    objective: str
    inputs: dict[str, Any] = field(default_factory=dict)
    trace: list[dict[str, Any]] = field(default_factory=list)
    retrieved_memories: list[dict[str, Any]] = field(default_factory=list)
    tool_outputs: list[dict[str, Any]] = field(default_factory=list)
    result: Any = None
    status: str = "running"
    error: str | None = None
    # Inbound webhook / external-trigger body, when this run was kicked
    # off with one. Surfaced into the planner's first system prompt so
    # the agent can act on it, and persisted on the run row for replay.
    trigger_payload: dict[str, Any] | None = None

    def to_checkpoint(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "task_name": self.task_name,
            "agent_name": self.agent_name,
            "objective": self.objective,
            "inputs": self.inputs,
            "trace": self.trace,
            "retrieved_memories": self.retrieved_memories,
            "tool_outputs": self.tool_outputs,
            "status": self.status,
            "result": self.result,
            "error": self.error,
            "trigger_payload": self.trigger_payload,
        }
