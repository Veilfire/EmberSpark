"""Sandbox backend protocol."""

from __future__ import annotations

from typing import Protocol

from spark.sandbox.policy import SandboxPolicy


class SandboxBackendProtocol(Protocol):
    name: str

    def available(self) -> bool: ...

    def build_argv(self, worker_argv: list[str], policy: SandboxPolicy) -> list[str]:
        """Return the argv that wraps the worker command with the sandbox."""
        ...
