"""Pydantic schemas for forensic snapshot payloads.

Each kind captures one slice of the chain of thought. All kinds
share the ``iteration`` + ``sequence`` pair so the viewer can
reconstruct the exact interleaving of prompt → model → tool →
memory events within a single run.

Everything here is pure data — no side effects, no DB. The writer
dumps these to JSON and encrypts the JSON with the run's age
identity before touching SQLite.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ForensicSnapshotKind(str, Enum):
    PROMPT = "prompt"
    MODEL = "model"
    TOOL = "tool"
    MEMORY_RETRIEVED = "memory_retrieved"
    MEMORY_WRITTEN = "memory_written"
    REFLECTION = "reflection"


class _BaseSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    iteration: int
    sequence: int
    span_id: int | None = None


class ForensicPromptSnapshot(_BaseSnapshot):
    """Assembled prompt + memory context sent into the model call."""

    kind: ForensicSnapshotKind = ForensicSnapshotKind.PROMPT
    system_prompt: str
    user_message: str | None = None
    memory_context: list[dict[str, Any]] = Field(default_factory=list)
    playbook_id: str | None = None
    char_count: int = 0
    message_count: int = 0


class ForensicModelSnapshot(_BaseSnapshot):
    """Model response for this iteration.

    Captures the full response text, any extended-thinking /
    reasoning blocks the provider exposed, and the raw tool-call
    requests the planner emitted.
    """

    kind: ForensicSnapshotKind = ForensicSnapshotKind.MODEL
    provider: str = ""
    model: str = ""
    content: str = ""
    reasoning_blocks: list[dict[str, Any]] = Field(default_factory=list)
    tool_calls_requested: list[dict[str, Any]] = Field(default_factory=list)
    stop_reason: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


class ForensicToolSnapshot(_BaseSnapshot):
    """One tool invocation + both raw and filtered results.

    The ``raw_result`` is what the plugin actually returned — pre
    privacy filtering. ``filtered_result`` is what the planner got
    to see. The delta is the redaction envelope.
    """

    kind: ForensicSnapshotKind = ForensicSnapshotKind.TOOL
    plugin: str
    args: dict[str, Any] = Field(default_factory=dict)
    raw_result: Any = None
    filtered_result: Any = None
    redactions: list[str] = Field(default_factory=list)
    error_code: str | None = None
    error_detail: dict[str, Any] | None = None
    duration_seconds: float | None = None


class ForensicMemorySnapshot(_BaseSnapshot):
    """Memory read or write. ``direction`` disambiguates the two."""

    kind: ForensicSnapshotKind = ForensicSnapshotKind.MEMORY_RETRIEVED
    direction: str = "retrieved"  # retrieved | written
    memory_ids: list[str] = Field(default_factory=list)
    records: list[dict[str, Any]] = Field(default_factory=list)


class ForensicReflectionSnapshot(_BaseSnapshot):
    """Post-run reflection output (lessons, patterns, follow-ups)."""

    kind: ForensicSnapshotKind = ForensicSnapshotKind.REFLECTION
    summary: str = ""
    lessons: list[str] = Field(default_factory=list)
    patterns: list[str] = Field(default_factory=list)
    follow_ups: list[str] = Field(default_factory=list)
