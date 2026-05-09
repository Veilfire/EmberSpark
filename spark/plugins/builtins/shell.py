"""Shell plugin — argv-only command execution against an operator allowlist.

Design rules:
- **Ships disabled with an empty command allowlist.** Operator must add each
  command explicitly via the Plugin Config UI.
- Never invokes a shell. Never accepts a raw string command. Every command is
  a named entry with an explicit ``argv_prefix`` and an ``allowed_flags`` list.
- Positional args are bounded by ``allowed_positional_count``.
- Final argv = ``argv_prefix + flags_in_allowlist + positional_args`` — built
  in-process before the sandbox runs the process.
- Output is capped by ``max_stdout_bytes`` and timed by ``timeout_seconds``.
- Runs inside the mandatory OS sandbox like every other plugin.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from spark.config.enums import DataScope, Permission, Sensitivity
from spark.privacy.guardrails import apply_guardrails


class ShellCommandSpec(BaseModel):
    """One operator-approved command, with its argv prefix and flag allowlist."""

    model_config = ConfigDict(extra="forbid")

    argv_prefix: list[str] = Field(min_length=1)
    allowed_flags: list[str] = Field(default_factory=list)
    allowed_positional_count: int = Field(default=0, ge=0, le=16)
    max_stdout_bytes: int = Field(default=1_000_000, ge=1, le=64_000_000)
    timeout_seconds: int = Field(default=10, ge=1, le=600)
    cwd_must_be_in: list[str] = Field(default_factory=list)


class ShellConfig(BaseModel):
    """Operator-edited config for the shell plugin."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    allowed_commands: dict[str, ShellCommandSpec] = Field(default_factory=dict)

    @field_validator("allowed_commands")
    @classmethod
    def _slug_keys(cls, v: dict[str, ShellCommandSpec]) -> dict[str, ShellCommandSpec]:
        for key in v:
            if not key.replace("_", "").replace("-", "").isalnum():
                raise ValueError(f"shell command name {key!r} must be alnum / _ / -")
        return v


class ShellArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command: str = Field(
        min_length=1,
        max_length=128,
        description="Argv-zero binary (e.g. 'ls', 'git'). Must appear in the operator's allowed_commands.",
    )
    flags: list[str] = Field(
        default_factory=list,
        max_length=32,
        description="Flags passed verbatim (e.g. ['--oneline', '-n', '20']). Each must match the per-command flag allowlist.",
    )
    positional: list[str] = Field(
        default_factory=list,
        max_length=16,
        description="Positional argv after flags (e.g. paths or refs). No shell interpolation.",
    )
    cwd: str | None = Field(
        default=None,
        description="Working directory. Defaults to the sandbox root; must be inside operator-allowed paths.",
    )


class ShellResult(BaseModel):
    command: str
    argv: list[str]
    exit_code: int
    stdout: str
    stderr: str
    truncated: bool
    duration_seconds: float


class ShellPlugin:
    name: ClassVar[str] = "shell"
    version: ClassVar[str] = "0.1.0"
    description: ClassVar[str] = (
        "Run operator-allowlisted commands via argv. Ships disabled by default."
    )
    input_schema: ClassVar[type[BaseModel]] = ShellArgs
    output_schema: ClassVar[type[BaseModel]] = ShellResult
    config_schema: ClassVar[type[BaseModel]] = ShellConfig
    required_permissions: ClassVar[frozenset[Permission]] = frozenset(
        {Permission.SUBPROCESS}
    )
    required_secrets: ClassVar[frozenset[str]] = frozenset()
    sensitivity: ClassVar[Sensitivity] = Sensitivity.MODERATE
    filter_output_before_model: ClassVar[bool] = True
    needs_network: ClassVar[bool] = False

    async def execute(self, args: ShellArgs, ctx: Any) -> ShellResult:
        plugin_config = getattr(ctx, "plugin_config", {}) or {}
        if not plugin_config.get("enabled", False):
            raise PermissionError("shell plugin is disabled in operator config")

        raw_commands = plugin_config.get("allowed_commands") or {}
        spec_raw = raw_commands.get(args.command)
        if spec_raw is None:
            raise PermissionError(
                f"shell command {args.command!r} is not in the operator allowlist"
            )
        spec = ShellCommandSpec.model_validate(spec_raw)

        # Flag allowlist: every supplied flag must appear in spec.allowed_flags.
        allowed_flags = set(spec.allowed_flags)
        for flag in args.flags:
            if flag not in allowed_flags:
                raise PermissionError(
                    f"flag {flag!r} not in allowed_flags for command {args.command!r}"
                )

        if len(args.positional) > spec.allowed_positional_count:
            raise PermissionError(
                f"{len(args.positional)} positional args exceeds the allowed "
                f"count ({spec.allowed_positional_count}) for command {args.command!r}"
            )

        # Reject NUL bytes and shell metacharacters anywhere in the argv.
        for piece in [*args.flags, *args.positional]:
            if "\x00" in piece:
                raise PermissionError("NUL byte in shell argv")
            if any(c in piece for c in (";", "&", "|", "`", "$", "\n", "\r")):
                raise PermissionError(
                    "shell metacharacters rejected — this plugin never invokes a shell"
                )

        cwd = None
        if args.cwd is not None:
            if not spec.cwd_must_be_in:
                raise PermissionError("cwd supplied but no cwd_must_be_in configured")
            resolved = Path(args.cwd).expanduser().resolve(strict=False)
            ok = False
            for base_str in spec.cwd_must_be_in:
                base = Path(base_str).expanduser().resolve()
                try:
                    resolved.relative_to(base)
                    ok = True
                    break
                except ValueError:
                    continue
            if not ok:
                raise PermissionError(f"cwd {resolved} not in cwd_must_be_in")
            cwd = str(resolved)

        argv = [*spec.argv_prefix, *args.flags, *args.positional]

        # Data-class guardrail on the assembled argv. The existing
        # operator allowlist is still the first line of defense; this
        # is additional pattern-matching for dangerous shapes
        # (`rm -rf /`, `sudo`, `curl | sh`, …) that may slip through
        # if the allowlist is broad.
        agent_name = getattr(ctx, "agent_name", None)
        await apply_guardrails(
            " ".join(argv),
            agent_name=agent_name,
            scope=DataScope.SHELL_ARGS,
        )

        import time

        start = time.monotonic()
        try:
            proc = await asyncio.wait_for(
                asyncio.create_subprocess_exec(
                    *argv,
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=cwd,
                    env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LC_ALL": "C.UTF-8"},
                ),
                timeout=spec.timeout_seconds,
            )
            stdout_bytes = bytearray()
            stderr_bytes = bytearray()
            truncated = False
            try:
                # Read stdout and stderr concurrently with an overall timeout.
                stdout_raw, stderr_raw = await asyncio.wait_for(
                    proc.communicate(),
                    timeout=spec.timeout_seconds,
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                raise
            stdout_bytes = bytearray(stdout_raw or b"")
            stderr_bytes = bytearray(stderr_raw or b"")
            if len(stdout_bytes) > spec.max_stdout_bytes:
                stdout_bytes = stdout_bytes[: spec.max_stdout_bytes]
                truncated = True
            if len(stderr_bytes) > spec.max_stdout_bytes:
                stderr_bytes = stderr_bytes[: spec.max_stdout_bytes]
                truncated = True
        except asyncio.TimeoutError as exc:
            raise TimeoutError(
                f"command {args.command!r} exceeded {spec.timeout_seconds}s"
            ) from exc

        duration = time.monotonic() - start
        return ShellResult(
            command=args.command,
            argv=argv,
            exit_code=proc.returncode if proc.returncode is not None else -1,
            stdout=bytes(stdout_bytes).decode("utf-8", errors="replace"),
            stderr=bytes(stderr_bytes).decode("utf-8", errors="replace"),
            truncated=truncated,
            duration_seconds=round(duration, 3),
        )
