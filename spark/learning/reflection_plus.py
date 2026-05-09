"""Enhanced reflection — produces playbook candidates + skill candidates.

The v1 reflector from `spark.reflection.reflector` produces lessons and memory
candidates. This module wraps it so the same pass also yields:

- a `PlaybookCandidate` when the run succeeded with a coherent tool sequence;
- a `SkillCandidate` when the agent acquired new knowledge about an external API.

The wrapper calls the existing reflector so Spark doesn't pay for two LLM
reflection passes per run.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class EnhancedReflectionInput:
    objective: str
    tool_sequence: list[str]
    success: bool
    duration_seconds: float
    tool_calls: int
    model_calls: int
    trace: list[dict[str, Any]]


@dataclass
class EnhancedReflectionResult:
    playbook_candidate: "PlaybookCandidate | None"  # noqa: F821 — forward
    skill_hints: list[dict[str, Any]]


def derive_playbook_candidate(
    inp: EnhancedReflectionInput,
    *,
    record_summary: str,
) -> "PlaybookCandidate | None":  # noqa: F821
    """Produce a playbook candidate if the run succeeded with a tool sequence."""
    from spark.learning.playbooks import PlaybookCandidate

    if not inp.success or not inp.tool_sequence:
        return None
    name = _name_from_objective(inp.objective)
    return PlaybookCandidate(
        name=name,
        description=record_summary[:240] or f"playbook for: {inp.objective[:80]}",
        objective_hint=inp.objective,
        tool_sequence=list(dict.fromkeys(inp.tool_sequence)),  # preserve order, dedup
    )


def _name_from_objective(objective: str) -> str:
    words = [w.lower() for w in objective.split() if w.isalnum()]
    key = "-".join(words[:5]) or "playbook"
    return f"pb-{key}"[:96]


def extract_skill_hints(trace: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Scan the run trace for tool events that mention an unknown API domain.

    This is a cheap pre-filter; the real skill discovery happens in
    `spark.skills.discovery`. We just surface candidate service names here so
    the engine can trigger discovery after the run.
    """
    hints: list[dict[str, Any]] = []
    seen: set[str] = set()
    for event in trace:
        if event.get("event") not in ("tool", "tool_error"):
            continue
        plugin = event.get("plugin", "")
        if plugin != "http_client":
            continue
        # In the existing engine trace we don't yet capture the host — extend
        # the trace emitter to include it. Downstream consumers must tolerate
        # missing fields.
        host = event.get("host") or event.get("args", {}).get("url", "")
        if host and host not in seen:
            seen.add(host)
            hints.append({"host": host, "source": "http_client"})
    return hints
