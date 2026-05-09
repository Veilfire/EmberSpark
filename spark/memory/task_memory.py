"""In-process task-scoped scratch memory. Cleared at task end."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class TaskMemory:
    scratch: dict[str, Any] = field(default_factory=dict)
    observations: list[str] = field(default_factory=list)

    def set(self, key: str, value: Any) -> None:
        self.scratch[key] = value

    def get(self, key: str, default: Any = None) -> Any:
        return self.scratch.get(key, default)

    def observe(self, note: str) -> None:
        self.observations.append(note)

    def clear(self) -> None:
        self.scratch.clear()
        self.observations.clear()
