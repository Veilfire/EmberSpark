"""Sandbox executor — the parent-side entry point.

Selects a backend, spawns a child process running the worker, writes the
RequestFrame over stdin, reads the ResponseFrame from stdout, enforces a hard
timeout, and returns the decoded response. Raises `SandboxUnavailable` at
startup if no backend works; we never silently fall back to an unsandboxed
execution.
"""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass

from spark.config.enums import SandboxBackend
from spark.errors import ErrorCode, SparkError
from spark.logging import EventType, get_logger
from spark.sandbox.backends.base import SandboxBackendProtocol
from spark.sandbox.backends.bubblewrap import BubblewrapBackend
from spark.sandbox.backends.nsjail import NsjailBackend
from spark.sandbox.backends.seatbelt import SeatbeltBackend
from spark.sandbox.ipc import RequestFrame, ResponseFrame
from spark.sandbox.policy import SandboxPolicy

log = get_logger("spark.sandbox")


class SandboxUnavailable(SparkError, RuntimeError):
    """No working sandbox backend could be found on the host."""

    def __init__(self, message: str, detail: dict | None = None) -> None:
        SparkError.__init__(
            self,
            ErrorCode.SANDBOX_UNAVAILABLE,
            message,
            detail=detail or {},
        )


class SandboxTimeout(SparkError, TimeoutError):
    def __init__(self, message: str, detail: dict | None = None) -> None:
        SparkError.__init__(
            self,
            ErrorCode.SANDBOX_TIMEOUT,
            message,
            detail=detail or {},
        )


class SandboxExecutionFailed(SparkError, RuntimeError):
    def __init__(self, error_type: str, message: str) -> None:
        self.error_type = error_type
        SparkError.__init__(
            self,
            ErrorCode.SANDBOX_EXEC_FAILED,
            f"{error_type}: {message}",
            detail={"error_type": error_type, "error_message": message},
        )


@dataclass
class _BackendRegistry:
    backends: list[SandboxBackendProtocol]

    def pick(self, requested: SandboxBackend) -> SandboxBackendProtocol:
        if requested == SandboxBackend.AUTO:
            for b in self.backends:
                if b.available():
                    return b
            raise SandboxUnavailable(
                "No sandbox backend available. Install bubblewrap (Linux) "
                "or ensure sandbox-exec is present (macOS). Windows is not supported."
            )
        name = requested.value
        for b in self.backends:
            if b.name == name:
                if not b.available():
                    raise SandboxUnavailable(f"Sandbox backend {name!r} not available on host")
                return b
        raise SandboxUnavailable(f"Unknown sandbox backend {name!r}")


_registry = _BackendRegistry(
    backends=[BubblewrapBackend(), NsjailBackend(), SeatbeltBackend()],
)


def check_available(backend: SandboxBackend = SandboxBackend.AUTO) -> str:
    """Raise SandboxUnavailable if no backend works. Returns selected name."""
    return _registry.pick(backend).name


async def run_sandboxed(
    request: RequestFrame,
    policy: SandboxPolicy,
) -> ResponseFrame:
    """Run the declared plugin inside a sandboxed subprocess.

    Parent writes one JSON frame on stdin; child writes one JSON frame on
    stdout. A hard wall-clock timeout is enforced in the parent.
    """
    if not policy.enabled:
        raise SandboxUnavailable("Sandbox is disabled in permissions; Spark refuses to run")

    backend = _registry.pick(policy.backend)
    worker_argv = [sys.executable, "-I", "-m", "spark.sandbox.worker"]
    argv = backend.build_argv(worker_argv, policy)

    log.info(
        "sandbox.spawn",
        event_type=EventType.SANDBOX_INVOKED,
        backend=backend.name,
        plugin=request.plugin_class,
        allow_network=policy.allow_network,
        ro_paths=[str(p) for p in policy.ro_paths],
        rw_paths=[str(p) for p in policy.rw_paths],
    )

    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(input=request.to_bytes()),
            timeout=policy.timeout_seconds,
        )
    except asyncio.TimeoutError as exc:
        proc.kill()
        await proc.wait()
        log.warning(
            "sandbox.timeout",
            event_type=EventType.SANDBOX_DENIED,
            plugin=request.plugin_class,
            timeout=policy.timeout_seconds,
        )
        raise SandboxTimeout(
            f"Sandboxed plugin {request.plugin_class!r} exceeded {policy.timeout_seconds}s",
            detail={
                "plugin": request.plugin_class,
                "timeout_seconds": policy.timeout_seconds,
            },
        ) from exc

    if proc.returncode != 0 and not stdout_bytes:
        msg = stderr_bytes.decode("utf-8", errors="replace").strip()
        raise SandboxExecutionFailed("SubprocessError", msg or f"exit {proc.returncode}")

    response = ResponseFrame.from_bytes(stdout_bytes)
    if not response.ok:
        # If the plugin raised a SparkError, reconstruct it on the parent
        # side so callers see the original code + detail + remediation.
        if response.error_code is not None:
            try:
                code = ErrorCode(response.error_code)
            except ValueError:
                code = ErrorCode.PLUGIN_RAISED
            raise SparkError(
                code=code,
                message=response.error or "plugin raised SparkError",
                detail=response.error_detail or {},
                remediation=response.error_remediation,
            )
        # Plugin raised a plain exception (no structured code). The
        # sandbox delivered a frame, so this is a plugin-level failure
        # (PLUGIN_RAISED), not infrastructure (SANDBOX_EXEC_FAILED).
        raise SparkError(
            code=ErrorCode.PLUGIN_RAISED,
            message=response.error or "unknown error",
            detail={
                "plugin": request.plugin_class,
                "error_type": response.error_type or "PluginError",
            },
        )
    return response
