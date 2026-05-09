"""Tool plugin contract.

Every tool plugin declares:
- Pydantic input/output schemas
- required permissions (which must be a subset of agent grants)
- required secrets (only these are injected into the ToolContext)
- its own sensitivity class
- whether its output should be filtered before model exposure

The `execute` method is async and runs inside the sandboxed child process.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar, Protocol, runtime_checkable

from pydantic import BaseModel

from spark.config.enums import Permission, Sensitivity
from spark.errors import ErrorCode, SparkError


class ToolContext(Protocol):
    """Scoped execution context handed to a plugin's `execute`.

    Fields populated by the parent process before sandbox dispatch:

    - ``secrets`` — only the secret values the plugin declared in
      ``required_secrets``.
    - ``privacy_mode`` — the agent's current privacy mode.
    - ``plugin_config`` — operator-only config knobs (fields in the plugin's
      ``config_schema`` that are NOT in its ``input_schema``).
    - ``scratch_path`` — the process data volume's scratch directory, or
      ``None`` if no data volume is active. Plugins may write intermediate
      blobs here (e.g. downloads before parsing, temp files).
    - ``deliverables_path`` — the process data volume's deliverables
      directory, or ``None`` if no data volume is active. Files written
      here are surfaced in the web UI's Downloads page and trigger
      notifications (subject to per-kind user preferences).
    """

    secrets: dict[str, str]
    privacy_mode: str
    plugin_config: dict[str, Any]
    scratch_path: Path | None
    deliverables_path: Path | None


@runtime_checkable
class ToolPlugin(Protocol):
    name: ClassVar[str]
    version: ClassVar[str]
    description: ClassVar[str]
    input_schema: ClassVar[type[BaseModel]]
    output_schema: ClassVar[type[BaseModel]]
    #: Operator-editable config. If a plugin does not take operator config,
    #: it may set this to ``type[BaseModel]`` pointing at an empty model.
    config_schema: ClassVar[type[BaseModel]]
    required_permissions: ClassVar[frozenset[Permission]]
    required_secrets: ClassVar[frozenset[str]]
    sensitivity: ClassVar[Sensitivity]
    filter_output_before_model: ClassVar[bool]
    needs_network: ClassVar[bool]

    async def execute(self, args: BaseModel, ctx: ToolContext) -> Any: ...


class PermissionDenied(SparkError, PermissionError):
    """Backward-compat alias for ``SparkError`` with a permission code.

    New code should ``raise SparkError(code=ErrorCode.X, ...)`` directly.
    ``except PermissionDenied`` / ``except PermissionError`` both still
    catch this; it's a subclass of both via multiple inheritance.
    """

    def __init__(
        self,
        message: str,
        *,
        code: ErrorCode = ErrorCode.PERMISSION_MISSING,
        detail: dict[str, Any] | None = None,
        remediation: str | None = None,
    ) -> None:
        SparkError.__init__(
            self,
            code,
            message,
            detail=detail,
            remediation=remediation,
        )


class BudgetExceeded(SparkError, RuntimeError):
    """Backward-compat alias for ``SparkError`` with a budget code.

    The default code is ``BUDGET_TOOL_EXCEEDED`` but callers should pass
    the specific budget counter that tripped.
    """

    def __init__(
        self,
        message: str,
        *,
        code: ErrorCode = ErrorCode.BUDGET_TOOL_EXCEEDED,
        detail: dict[str, Any] | None = None,
        remediation: str | None = None,
    ) -> None:
        SparkError.__init__(
            self,
            code,
            message,
            detail=detail,
            remediation=remediation,
        )
