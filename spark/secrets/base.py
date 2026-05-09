"""Shared protocol + exceptions for the secrets subsystem.

Leaf module with **no intra-package imports** — both ``env_backend``
and ``manager`` import from here, breaking the circular dependency
that would otherwise arise from ``env_backend → manager → env_backend``.
"""

from __future__ import annotations

from typing import Protocol

from pydantic import SecretStr


class SecretNotFound(KeyError):
    """Raised when a secret is not resolvable by any configured backend."""


class SecretBackend(Protocol):
    """Structural protocol preserved for backward compatibility.

    New code should use ``SecretManager`` directly; this protocol is kept
    so third-party plugins that implemented it in v1 keep working.
    """

    name: str

    def get(self, name: str) -> SecretStr: ...
    def list_names(self) -> list[str]: ...
    def available(self, name: str) -> bool: ...
