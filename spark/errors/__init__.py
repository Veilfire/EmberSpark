"""Structured error codes (H1.4).

Every permission denial, budget exhaustion, sandbox refusal, SSRF block,
path traversal, and invalid input carries a stable :class:`ErrorCode`
enum value plus a small structured ``detail`` payload. The engine
serializes these into the next model message so the planner can branch
on a well-known identifier instead of string-parsing English.

Usage in plugin code::

    from spark.errors import ErrorCode, SparkError

    raise SparkError(
        code=ErrorCode.METHOD_NOT_ALLOWED,
        message=f"method {method!r} not allowed on {host!r}",
        detail={"plugin": "http_tool", "method": method, "host": host},
        remediation="Add the method to rule.allowed_methods in the operator config",
    )

Existing ``PermissionDenied`` / ``BudgetExceeded`` / ``UrlDenied`` /
``PathDenied`` / ``SandboxUnavailable`` / ``SandboxTimeout`` /
``SandboxExecutionFailed`` all inherit from :class:`SparkError` so
legacy ``except PermissionError`` catches keep working.
"""

from __future__ import annotations

from spark.errors.codes import ErrorCode, SparkError

__all__ = ["ErrorCode", "SparkError"]
