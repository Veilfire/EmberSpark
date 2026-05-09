"""Tests for the shell plugin argv builder + operator allowlist."""

from __future__ import annotations

import pytest

from spark.plugins.builtins.shell import (
    ShellArgs,
    ShellCommandSpec,
    ShellConfig,
    ShellPlugin,
)


class _Ctx:
    def __init__(self, config: dict) -> None:
        self.secrets: dict[str, str] = {}
        self.privacy_mode = "strict"
        self.plugin_config = config


@pytest.mark.asyncio
async def test_disabled_by_default() -> None:
    plugin = ShellPlugin()
    ctx = _Ctx(ShellConfig().model_dump())
    with pytest.raises(PermissionError, match="disabled"):
        await plugin.execute(ShellArgs(command="echo"), ctx)


@pytest.mark.asyncio
async def test_unknown_command_rejected() -> None:
    plugin = ShellPlugin()
    ctx = _Ctx(
        ShellConfig(
            enabled=True,
            allowed_commands={
                "echo": ShellCommandSpec(
                    argv_prefix=["echo"], allowed_positional_count=4
                )
            },
        ).model_dump()
    )
    with pytest.raises(PermissionError, match="not in the operator allowlist"):
        await plugin.execute(ShellArgs(command="rm"), ctx)


@pytest.mark.asyncio
async def test_unknown_flag_rejected() -> None:
    plugin = ShellPlugin()
    ctx = _Ctx(
        ShellConfig(
            enabled=True,
            allowed_commands={
                "ls": ShellCommandSpec(
                    argv_prefix=["ls"],
                    allowed_flags=["-l", "-a"],
                    allowed_positional_count=2,
                )
            },
        ).model_dump()
    )
    with pytest.raises(PermissionError, match="allowed_flags"):
        await plugin.execute(
            ShellArgs(command="ls", flags=["--evil"]), ctx
        )


@pytest.mark.asyncio
async def test_shell_metacharacters_rejected() -> None:
    plugin = ShellPlugin()
    ctx = _Ctx(
        ShellConfig(
            enabled=True,
            allowed_commands={
                "ls": ShellCommandSpec(
                    argv_prefix=["ls"], allowed_positional_count=2
                )
            },
        ).model_dump()
    )
    with pytest.raises(PermissionError, match="metacharacters"):
        await plugin.execute(
            ShellArgs(command="ls", positional=["foo;rm -rf /"]),
            ctx,
        )


@pytest.mark.asyncio
async def test_excess_positional_rejected() -> None:
    plugin = ShellPlugin()
    ctx = _Ctx(
        ShellConfig(
            enabled=True,
            allowed_commands={
                "echo": ShellCommandSpec(
                    argv_prefix=["echo"], allowed_positional_count=1
                )
            },
        ).model_dump()
    )
    with pytest.raises(PermissionError, match="positional args exceeds"):
        await plugin.execute(
            ShellArgs(command="echo", positional=["a", "b", "c"]),
            ctx,
        )
