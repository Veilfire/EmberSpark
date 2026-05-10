"""`ErrorCode` enum + `SparkError` dataclass-style exception.

See ``spark/errors/__init__.py`` for the intent.
"""

from __future__ import annotations

from enum import Enum
from typing import Any


class ErrorCode(str, Enum):
    """Stable machine-readable error classes.

    String values are prefixed with ``SPK_E_`` so they don't collide with
    any other namespace and are instantly recognizable in logs.
    """

    # ------------------------------------------------------------------
    # Layer 1 — plugin allowlist
    # ------------------------------------------------------------------
    PLUGIN_NOT_ALLOWED = "SPK_E_PLUGIN_NOT_ALLOWED"
    PLUGIN_NOT_REGISTERED = "SPK_E_PLUGIN_NOT_REGISTERED"

    # ------------------------------------------------------------------
    # Layer 2 — permission grants
    # ------------------------------------------------------------------
    PERMISSION_MISSING = "SPK_E_PERMISSION_MISSING"

    # ------------------------------------------------------------------
    # Layer 3 — budgets
    # ------------------------------------------------------------------
    BUDGET_ITER_EXCEEDED = "SPK_E_BUDGET_ITER_EXCEEDED"
    BUDGET_MODEL_EXCEEDED = "SPK_E_BUDGET_MODEL_EXCEEDED"
    BUDGET_TOOL_EXCEEDED = "SPK_E_BUDGET_TOOL_EXCEEDED"
    BUDGET_TOKEN_EXCEEDED = "SPK_E_BUDGET_TOKEN_EXCEEDED"
    BUDGET_WALL_CLOCK_EXCEEDED = "SPK_E_BUDGET_WALL_CLOCK_EXCEEDED"
    BUDGET_COST_HARD_STOP = "SPK_E_BUDGET_COST_HARD_STOP"

    # ------------------------------------------------------------------
    # Layer 4 — operator config merge / input validation
    # ------------------------------------------------------------------
    INPUT_SCHEMA_INVALID = "SPK_E_INPUT_SCHEMA_INVALID"
    OUTPUT_SCHEMA_INVALID = "SPK_E_OUTPUT_SCHEMA_INVALID"
    OPERATOR_OVERRIDE_REFUSED = "SPK_E_OPERATOR_OVERRIDE_REFUSED"

    # ------------------------------------------------------------------
    # Layer 5 — sandbox
    # ------------------------------------------------------------------
    SANDBOX_UNAVAILABLE = "SPK_E_SANDBOX_UNAVAILABLE"
    SANDBOX_TIMEOUT = "SPK_E_SANDBOX_TIMEOUT"
    SANDBOX_EXEC_FAILED = "SPK_E_SANDBOX_EXEC_FAILED"

    # ------------------------------------------------------------------
    # Network (SSRF defense + plugin-level method/response guards)
    # ------------------------------------------------------------------
    URL_DENIED = "SPK_E_URL_DENIED"
    URL_METADATA_BLOCKED = "SPK_E_URL_METADATA_BLOCKED"
    URL_PRIVATE_IP = "SPK_E_URL_PRIVATE_IP"
    URL_IDN_INVALID = "SPK_E_URL_IDN_INVALID"
    METHOD_NOT_ALLOWED = "SPK_E_METHOD_NOT_ALLOWED"
    RESPONSE_TOO_LARGE = "SPK_E_RESPONSE_TOO_LARGE"

    # ------------------------------------------------------------------
    # Filesystem
    # ------------------------------------------------------------------
    PATH_DENIED = "SPK_E_PATH_DENIED"
    PATH_TRAVERSAL = "SPK_E_PATH_TRAVERSAL"
    PATH_SYMLINK_REFUSED = "SPK_E_PATH_SYMLINK_REFUSED"
    FILE_NOT_FOUND = "SPK_E_FILE_NOT_FOUND"
    FILE_TOO_LARGE = "SPK_E_FILE_TOO_LARGE"

    # ------------------------------------------------------------------
    # Secrets
    # ------------------------------------------------------------------
    SECRET_NOT_FOUND = "SPK_E_SECRET_NOT_FOUND"
    SECRET_PROVIDER_UNAVAILABLE = "SPK_E_SECRET_PROVIDER_UNAVAILABLE"

    # ------------------------------------------------------------------
    # Runtime (task lifecycle)
    # ------------------------------------------------------------------
    FROZEN = "SPK_E_FROZEN"
    APPROVAL_REQUIRED = "SPK_E_APPROVAL_REQUIRED"
    RUN_WINDOW_CLOSED = "SPK_E_RUN_WINDOW_CLOSED"
    DLQ_UNACKED = "SPK_E_DLQ_UNACKED"

    # ------------------------------------------------------------------
    # Data Classification Guardrails
    # ------------------------------------------------------------------
    DATA_CLASS_BLOCKED = "SPK_E_DATA_CLASS_BLOCKED"
    DATA_CLASS_GRANT_REQUIRED = "SPK_E_DATA_CLASS_GRANT_REQUIRED"

    # ------------------------------------------------------------------
    # Plugin-internal catch-all
    # ------------------------------------------------------------------
    PLUGIN_RAISED = "SPK_E_PLUGIN_RAISED"


#: Short, stable remediation hints keyed by ``ErrorCode``. These travel
#: to the model alongside the ``detail`` payload so the planner knows
#: what to try next without the operator pre-engineering a prompt.
_DEFAULT_REMEDIATION: dict[ErrorCode, str] = {
    ErrorCode.PLUGIN_NOT_ALLOWED: "Add the plugin to the agent YAML's `spec.plugins.allow`.",
    ErrorCode.PLUGIN_NOT_REGISTERED: "Install the plugin package and restart the runtime.",
    ErrorCode.PERMISSION_MISSING: "Add the missing permission to `spec.permissions.grants`.",
    ErrorCode.BUDGET_ITER_EXCEEDED: "Raise `runtime.max_iterations` or investigate planner looping.",
    ErrorCode.BUDGET_MODEL_EXCEEDED: "Raise `runtime.max_model_calls` or trim the task scope.",
    ErrorCode.BUDGET_TOOL_EXCEEDED: "Raise `runtime.max_tool_calls` or reduce tool invocations.",
    ErrorCode.BUDGET_TOKEN_EXCEEDED: "Raise `runtime.max_tokens_per_run` or shorten the prompts and tool outputs.",
    ErrorCode.BUDGET_WALL_CLOCK_EXCEEDED: "Raise `runtime.max_runtime_seconds` or investigate slow operations.",
    ErrorCode.BUDGET_COST_HARD_STOP: "Wait for the budget period to reset, or raise the limit in the Cost page.",
    ErrorCode.INPUT_SCHEMA_INVALID: "Send only fields in the plugin's input_schema.",
    ErrorCode.OUTPUT_SCHEMA_INVALID: "This is a plugin bug — file an issue.",
    ErrorCode.OPERATOR_OVERRIDE_REFUSED: "The operator has locked this field in plugin config; stop trying to override it.",
    ErrorCode.SANDBOX_UNAVAILABLE: "Install bubblewrap (Linux) or confirm sandbox-exec (macOS) is present.",
    ErrorCode.SANDBOX_TIMEOUT: "Raise `permissions.sandbox.timeout_seconds` or shorten the operation.",
    ErrorCode.SANDBOX_EXEC_FAILED: "This is often transient — retry. If persistent, check sandbox logs.",
    ErrorCode.URL_DENIED: "Add the host to the operator's plugin config `allow_hosts` / `rules`.",
    ErrorCode.URL_METADATA_BLOCKED: "This IP is a cloud metadata address — it is never reachable from Spark.",
    ErrorCode.URL_PRIVATE_IP: "Use a public DNS name or request an internal-IP grant in the Security Center.",
    ErrorCode.URL_IDN_INVALID: "Use the punycode form of the hostname.",
    ErrorCode.METHOD_NOT_ALLOWED: "Ask the operator to add the HTTP method to the plugin's per-host rule.",
    ErrorCode.RESPONSE_TOO_LARGE: "Ask for fewer results, paginate, or raise `max_response_bytes`.",
    ErrorCode.PATH_DENIED: "Ask the operator to add the path to the plugin's `allow_paths`.",
    ErrorCode.PATH_TRAVERSAL: "Refuse any path containing `..` — use absolute paths or stay inside the workspace.",
    ErrorCode.PATH_SYMLINK_REFUSED: "Symlinks are refused to prevent bind-mount escape.",
    ErrorCode.FILE_NOT_FOUND: "Check the path; the operator may not have created it yet.",
    ErrorCode.FILE_TOO_LARGE: "Raise `max_read_bytes` in the filesystem plugin config or read in chunks.",
    ErrorCode.SECRET_NOT_FOUND: "Populate the secret via `spark secrets set <name>`.",
    ErrorCode.SECRET_PROVIDER_UNAVAILABLE: "Initialize the age vault via `spark secrets init-age-vault`.",
    ErrorCode.FROZEN: "The runtime is frozen by operator action — wait for unfreeze.",
    ErrorCode.APPROVAL_REQUIRED: "The task is paused awaiting operator approval in the Scheduler page.",
    ErrorCode.RUN_WINDOW_CLOSED: "The task is configured to run only in a specific time window.",
    ErrorCode.DLQ_UNACKED: "The task is in the dead-letter queue — operator must ack it first.",
    ErrorCode.DATA_CLASS_BLOCKED: (
        "The operation was refused because a data-classification guardrail "
        "matched. Ask the operator to lower the class level or grant an "
        "unlimited carve-out in Security Center → Data Classes."
    ),
    ErrorCode.DATA_CLASS_GRANT_REQUIRED: (
        "This data class is blocked by policy. Operator must create a "
        "DataClassGrant for this agent + class + scope to allow it."
    ),
    ErrorCode.PLUGIN_RAISED: "Plugin-internal failure — check the plugin's logs.",
}


class SparkError(Exception):
    """Base runtime error with a machine-readable code.

    Deliberately NOT a ``dataclass`` so it plays nicely with
    ``except PermissionError`` etc. when the subclass uses multiple
    inheritance (see ``PermissionDenied`` below which inherits from
    both ``PermissionError`` and this).
    """

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        detail: dict[str, Any] | None = None,
        remediation: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail or {}
        self.remediation = remediation or _DEFAULT_REMEDIATION.get(code)

    def to_dict(self) -> dict[str, Any]:
        """Serialize for structured logs and model-facing payloads.

        ``tuning`` is a list of :class:`TuningOption` records the
        Failure Inspector renders. The model-facing layers (planner
        prompt, structured tool result) ignore it; only the operator
        UI consumes it. The legacy ``remediation`` string stays as the
        model-facing hint — short, no UI assumptions.
        """
        from spark.errors.remediation import options_for  # noqa: PLC0415

        # Pass the message through to the catalogue so legacy raise
        # sites that didn't bother with a structured ``detail`` (just a
        # human-readable string like ``UrlDenied("Host 'foo' ...")``)
        # still get reasonable extraction.
        detail_with_msg = dict(self.detail)
        detail_with_msg.setdefault("_message", self.message)
        return {
            "code": self.code.value,
            "message": self.message,
            "detail": self.detail,
            "remediation": self.remediation,
            "tuning": [opt.to_dict() for opt in options_for(self.code, detail_with_msg)],
        }

    def __str__(self) -> str:
        return f"[{self.code.value}] {self.message}"
