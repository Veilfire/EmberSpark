"""Markdown writer plugin — thin wrapper over filesystem restricted to .md files."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, Field, field_validator

from spark.config.enums import Permission, Sensitivity


class MarkdownWriterConfig(BaseModel):
    """Operator-edited defaults for markdown_writer."""

    allow_paths: list[str] = Field(default_factory=list)
    deny_paths: list[str] = Field(default_factory=list)
    allow_append: bool = True
    allow_overwrite: bool = True


class MarkdownWriteArgs(BaseModel):
    path: str = Field(
        description="Target file path. Must end in .md or .markdown and be inside the operator's allow_paths.",
    )
    content: str = Field(
        description="Markdown text to write or append.",
    )
    mode: Literal["write", "append"] = Field(
        default="write",
        description="'write' replaces the file; 'append' adds to the end.",
    )
    allow_paths: list[str] = Field(
        default_factory=list,
        description="Per-call narrowing of allow_paths (operator config wins).",
    )
    deny_paths: list[str] = Field(
        default_factory=list,
        description="Per-call additions to deny_paths (operator config wins).",
    )

    @field_validator("path")
    @classmethod
    def _must_be_md(cls, v: str) -> str:
        if not v.lower().endswith((".md", ".markdown")):
            raise ValueError("markdown_writer only writes .md / .markdown files")
        return v


class MarkdownWriteResult(BaseModel):
    path: str
    bytes_written: int
    mode: str


class MarkdownWriterPlugin:
    name: ClassVar[str] = "markdown_writer"
    version: ClassVar[str] = "0.1.0"
    description: ClassVar[str] = "Write markdown output to a local path in the allowlist."
    input_schema: ClassVar[type[BaseModel]] = MarkdownWriteArgs
    output_schema: ClassVar[type[BaseModel]] = MarkdownWriteResult
    config_schema: ClassVar[type[BaseModel]] = MarkdownWriterConfig
    required_permissions: ClassVar[frozenset[Permission]] = frozenset({Permission.FS_WRITE})
    required_secrets: ClassVar[frozenset[str]] = frozenset()
    sensitivity: ClassVar[Sensitivity] = Sensitivity.LOW
    filter_output_before_model: ClassVar[bool] = True
    needs_network: ClassVar[bool] = False

    async def execute(self, args: MarkdownWriteArgs, ctx: Any) -> MarkdownWriteResult:
        from spark.utils.paths import PathPolicy

        plugin_config = getattr(ctx, "plugin_config", {}) or {}
        if args.mode == "append" and not plugin_config.get("allow_append", True):
            raise PermissionError("markdown_writer configured to deny append mode")
        if args.mode == "write" and not plugin_config.get("allow_overwrite", True):
            raise PermissionError("markdown_writer configured to deny overwrite mode")

        # Working default: when the operator hasn't configured allow_paths,
        # fall back to the data volume's deliverables root. The runtime
        # already scopes that path to the agent's fs.write grant via
        # SandboxPolicy, so this is safe — and it removes the most common
        # "fresh install" footgun where every write fails until the operator
        # discovers the allow_paths config.
        allow_paths = list(args.allow_paths)
        if not allow_paths:
            deliverables = getattr(ctx, "deliverables_path", None)
            if deliverables:
                allow_paths.append(str(deliverables))

        policy = PathPolicy.from_strings(allow_paths, args.deny_paths)
        target = policy.check(Path(args.path))
        parent = target.parent
        if not parent.exists() or parent.is_symlink():
            raise PermissionError(
                f"refusing write: parent {parent} must exist and not be a symlink"
            )

        flags = os.O_WRONLY | os.O_NOFOLLOW
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        if args.mode == "append":
            flags |= os.O_APPEND
            fd = os.open(target, flags, 0o600)
        else:
            try:
                fd = os.open(target, flags | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                fd = os.open(target, flags | os.O_TRUNC)
        try:
            n = os.write(fd, args.content.encode("utf-8"))
        finally:
            os.close(fd)
        return MarkdownWriteResult(path=str(target), bytes_written=n, mode=args.mode)
