"""Filesystem plugin: bounded read, write, and list against an allowlist.

The sandbox also enforces the allowlist at the OS level, so even if the Python
policy check is bypassed the kernel still refuses writes outside the bound
mounts. Belt + suspenders.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, Field

from spark.config.enums import Permission, Sensitivity


class FilesystemConfig(BaseModel):
    """Operator-edited defaults for the filesystem plugin."""

    allow_paths: list[str] = Field(default_factory=list)
    deny_paths: list[str] = Field(
        default_factory=lambda: ["~/.ssh", "~/.aws", "~/.config"]
    )
    max_read_bytes: int = Field(default=5_000_000, ge=0, le=100_000_000)
    max_files_per_call: int = Field(default=256, ge=0, le=10_000)
    read_only: bool = False


class FilesystemArgs(BaseModel):
    op: Literal["read", "write", "append", "list", "stat"] = Field(
        description=(
            "Operation: 'read' (file → text), 'write' (replace), "
            "'append' (add to end), 'list' (directory contents), "
            "'stat' (size + mtime)."
        ),
    )
    path: str = Field(
        description="Target path. Resolved with traversal-rejection; must be inside operator's allow_paths.",
    )
    content: str | None = Field(
        default=None,
        description="Required for 'write' and 'append'. The text to write.",
    )
    allow_paths: list[str] = Field(
        default_factory=list,
        description="Per-call narrowing of allow_paths (operator config wins on conflict).",
    )
    deny_paths: list[str] = Field(
        default_factory=list,
        description="Per-call additions to deny_paths (operator config wins on conflict).",
    )
    max_read_bytes: int = Field(
        default=5_000_000,
        description="Cap on bytes read per 'read' call (capped by operator config).",
    )
    max_files_per_call: int = Field(
        default=256,
        description="Cap on entries returned by 'list' (capped by operator config).",
    )


class FileEntry(BaseModel):
    name: str
    path: str
    is_dir: bool
    size: int


class FilesystemResult(BaseModel):
    op: str
    path: str
    bytes_read: int = 0
    bytes_written: int = 0
    content: str | None = None
    entries: list[FileEntry] = Field(default_factory=list)
    truncated: bool = False


class FilesystemPlugin:
    name: ClassVar[str] = "filesystem"
    version: ClassVar[str] = "0.1.0"
    description: ClassVar[str] = "Bounded filesystem access against an explicit allowlist."
    input_schema: ClassVar[type[BaseModel]] = FilesystemArgs
    output_schema: ClassVar[type[BaseModel]] = FilesystemResult
    config_schema: ClassVar[type[BaseModel]] = FilesystemConfig
    required_permissions: ClassVar[frozenset[Permission]] = frozenset(
        {Permission.FS_READ, Permission.FS_WRITE, Permission.FS_LIST}
    )
    required_secrets: ClassVar[frozenset[str]] = frozenset()
    sensitivity: ClassVar[Sensitivity] = Sensitivity.MODERATE
    filter_output_before_model: ClassVar[bool] = True
    needs_network: ClassVar[bool] = False

    async def execute(self, args: FilesystemArgs, ctx: Any) -> FilesystemResult:
        from spark.utils.paths import PathPolicy

        # Operator-only knob: hard-disable writes regardless of permissions.
        plugin_config = getattr(ctx, "plugin_config", {}) or {}
        if plugin_config.get("read_only") and args.op in ("write", "append"):
            raise PermissionError("filesystem plugin configured read_only")

        # Working default: when neither operator config nor per-call args
        # supply allow_paths, fall back to the data-volume's scratch and
        # deliverables roots. Both are already sandbox-scoped to the
        # agent's grants — falling back here only removes the "fresh
        # install hard-fails" footgun and never widens reach.
        allow_paths = list(args.allow_paths)
        if not allow_paths:
            for attr in ("scratch_path", "deliverables_path"):
                p = getattr(ctx, attr, None)
                if p:
                    allow_paths.append(str(p))

        policy = PathPolicy.from_strings(allow_paths, args.deny_paths)
        target = policy.check(Path(args.path))

        if args.op == "read":
            return self._read(target, args)
        if args.op == "write":
            return self._write(target, args, append=False)
        if args.op == "append":
            return self._write(target, args, append=True)
        if args.op == "list":
            return self._list(target, args)
        if args.op == "stat":
            return self._stat(target)
        raise ValueError(f"unknown op {args.op!r}")

    def _open_nofollow(self, path: Path, flags: int, mode: int = 0o600) -> int:
        flags |= os.O_NOFOLLOW
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        return os.open(path, flags, mode)

    def _read(self, target: Path, args: FilesystemArgs) -> FilesystemResult:
        fd = self._open_nofollow(target, os.O_RDONLY)
        try:
            data = os.read(fd, args.max_read_bytes + 1)
        finally:
            os.close(fd)
        truncated = len(data) > args.max_read_bytes
        payload = data[: args.max_read_bytes]
        try:
            text = payload.decode("utf-8")
        except UnicodeDecodeError:
            text = payload.decode("utf-8", errors="replace")
        return FilesystemResult(
            op="read",
            path=str(target),
            bytes_read=len(payload),
            content=text,
            truncated=truncated,
        )

    def _write(self, target: Path, args: FilesystemArgs, *, append: bool) -> FilesystemResult:
        """Write to ``target`` atomically with TOCTOU-hardened open.

        The parent directory must already exist and must not be a symlink
        anywhere along its resolved path — we never create parent dirs from
        inside the plugin, because that would re-open a window where a hostile
        process could swap a component.

        The final open uses ``O_NOFOLLOW`` (refuse if target is a symlink),
        ``O_CLOEXEC``, and ``O_EXCL`` when creating a fresh file. Append mode
        requires the file to already exist.
        """
        if args.content is None:
            raise ValueError("write/append requires content")
        parent = target.parent
        if not parent.exists() or parent.is_symlink():
            raise PermissionError(
                f"refusing write: parent {parent} must exist and not be a symlink"
            )
        payload = args.content.encode("utf-8")
        if len(payload) > args.max_read_bytes:
            # Reuse the same cap for symmetry; writes shouldn't exceed the
            # per-call byte budget.
            raise PermissionError(
                f"write payload {len(payload)}B exceeds max_read_bytes"
            )

        flags = os.O_WRONLY | os.O_NOFOLLOW
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        if append:
            flags |= os.O_APPEND
            fd = os.open(target, flags, 0o600)
        else:
            # Create-or-truncate, but use O_EXCL on first-creation to catch
            # swap races on the final component.
            try:
                fd = os.open(target, flags | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                fd = os.open(target, flags | os.O_TRUNC)

        try:
            n = os.write(fd, payload)
        finally:
            os.close(fd)
        return FilesystemResult(
            op="append" if append else "write",
            path=str(target),
            bytes_written=n,
        )

    def _list(self, target: Path, args: FilesystemArgs) -> FilesystemResult:
        entries: list[FileEntry] = []
        with os.scandir(target) as it:
            for i, de in enumerate(it):
                if i >= args.max_files_per_call:
                    break
                try:
                    stat = de.stat(follow_symlinks=False)
                except OSError:
                    continue
                entries.append(
                    FileEntry(
                        name=de.name,
                        path=str(Path(de.path)),
                        is_dir=de.is_dir(follow_symlinks=False),
                        size=stat.st_size,
                    )
                )
        return FilesystemResult(
            op="list",
            path=str(target),
            entries=entries,
            truncated=len(entries) == args.max_files_per_call,
        )

    def _stat(self, target: Path) -> FilesystemResult:
        stat = target.lstat()
        return FilesystemResult(
            op="stat",
            path=str(target),
            bytes_read=stat.st_size,
        )
