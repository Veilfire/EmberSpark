"""Git plugin — narrow, structured wrapper around the `git` binary.

Shipping a dedicated git plugin (instead of relying on `shell` with
`git-log` / `git-status` allowlists) gives the agent:

- a structured output schema per op (parsed commits, parsed status entries)
- a single operator knob (``allow_write``) to switch between read-only and
  read-write modes
- uniform permission gating (``fs.read``, ``fs.write`` if writes allowed,
  ``subprocess`` always)

The plugin never runs anything other than ``git`` with argv lists — no
shell interpretation, no metacharacters, no environment leakage.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from spark.config.enums import Permission, Sensitivity

Op = Literal["status", "log", "diff", "show", "branch", "add", "commit"]


class GitConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    allow_repos: list[Path] = Field(default_factory=list)
    allow_write: bool = False
    max_log_entries: int = Field(default=500, ge=1, le=10_000)
    max_diff_bytes: int = Field(default=1_000_000, ge=1, le=100_000_000)
    max_status_entries: int = Field(default=500, ge=1, le=10_000)
    git_binary: str = Field(default="git", max_length=256)
    timeout_seconds: int = Field(default=30, ge=1, le=600)


class GitArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    op: Op = Field(
        description=(
            "Git operation: 'status' (working-tree state), 'log' (commits), "
            "'diff' (changes), 'show' (commit / blob), 'branch' (list), "
            "'add' (stage paths), 'commit' (create commit). "
            "Write ops require allow_write=true on the plugin config."
        ),
    )
    repo: Path = Field(
        description="Repository root. Must be inside the operator's allow_repos.",
    )
    ref: str | None = Field(
        default=None,
        max_length=256,
        description="Branch / tag / commit-SHA. Used by 'log', 'diff', 'show'.",
    )
    path: str | None = Field(
        default=None,
        max_length=1024,
        description="Path filter inside the repo (for 'log', 'diff', 'show', 'add').",
    )
    # See WebSearchArgs for why we clamp via a validator instead of
    # ``Field(ge=1, le=10000)`` — Bedrock rejects ``minimum``/``maximum``
    # on ``number`` types in the tool-binding JSON Schema, which makes
    # the planner see no tools at all.
    limit: int | None = Field(
        default=None,
        description="Max entries for 'log' / 'status' (clamped to 1..10000).",
    )
    since: str | None = Field(
        default=None,
        max_length=64,
        description="Lower-bound date / ref for 'log' (e.g. '2 weeks ago', 'v1.0').",
    )
    until: str | None = Field(
        default=None,
        max_length=64,
        description="Upper-bound date / ref for 'log'.",
    )
    message: str | None = Field(
        default=None,
        max_length=4000,
        description="Commit message — required by 'commit'.",
    )

    @field_validator("limit")
    @classmethod
    def _clamp_limit(cls, v: int | None) -> int | None:
        if v is None:
            return None
        if v < 1:
            return 1
        if v > 10_000:
            return 10_000
        return v


class GitCommit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sha: str
    author: str
    date: str
    subject: str


class GitStatusEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str
    index: str
    worktree: str


class GitResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    op: Op
    repo: str
    commits: list[GitCommit] | None = None
    status: list[GitStatusEntry] | None = None
    diff: str | None = None
    current_branch: str | None = None
    branches: list[str] | None = None
    stdout: str | None = None
    truncated: bool = False


# Git refs permitted by the plugin. We deliberately reject ``..`` sequences
# (which git parses as commit ranges) and leading ``-`` (which git can
# interpret as a flag even after ``--``). Also rejects ``@{`` reflog syntax
# and everything `git check-ref-format` would consider invalid.
_SAFE_REF = re.compile(r"^(?!-)[A-Za-z0-9._/\-]+$")
_REJECTED_REF_SUBSTRINGS = ("..", "@{", "\\", "//")


class GitPlugin:
    name: ClassVar[str] = "git"
    version: ClassVar[str] = "0.1.0"
    description: ClassVar[str] = (
        "Narrow git operations: status / log / diff / show / branch / add / "
        "commit. Argv-only, per-op structured output."
    )
    input_schema: ClassVar[type[BaseModel]] = GitArgs
    output_schema: ClassVar[type[BaseModel]] = GitResponse
    config_schema: ClassVar[type[BaseModel]] = GitConfig
    required_permissions: ClassVar[frozenset[Permission]] = frozenset(
        {Permission.FS_READ, Permission.SUBPROCESS}
    )
    required_secrets: ClassVar[frozenset[str]] = frozenset()
    sensitivity: ClassVar[Sensitivity] = Sensitivity.MODERATE
    filter_output_before_model: ClassVar[bool] = True
    needs_network: ClassVar[bool] = False

    async def execute(self, args: GitArgs, ctx: Any) -> GitResponse:
        cfg = getattr(ctx, "plugin_config", {}) or {}
        allow_repos = [Path(p).expanduser().resolve() for p in (cfg.get("allow_repos") or [])]
        allow_write = bool(cfg.get("allow_write", False))
        max_log = int(cfg.get("max_log_entries") or 500)
        max_diff_bytes = int(cfg.get("max_diff_bytes") or 1_000_000)
        max_status = int(cfg.get("max_status_entries") or 500)
        git_binary = cfg.get("git_binary") or "git"
        timeout = int(cfg.get("timeout_seconds") or 30)

        # Repo must be exactly one of the allowlisted roots OR a descendant.
        repo = Path(args.repo).expanduser().resolve()
        if not allow_repos:
            raise PermissionError("git: no repos in operator allowlist")
        if not any(_is_within(repo, root) for root in allow_repos):
            raise PermissionError(f"git: repo {repo} not in allowlist")
        if not (repo / ".git").exists():
            raise PermissionError(f"git: {repo} is not a git repository")

        write_ops = {"add", "commit"}
        if args.op in write_ops and not allow_write:
            raise PermissionError(
                f"git: op {args.op!r} requires allow_write=true in operator config"
            )

        if args.ref is not None:
            if not _SAFE_REF.match(args.ref):
                raise PermissionError(f"git: unsafe ref {args.ref!r}")
            if any(s in args.ref for s in _REJECTED_REF_SUBSTRINGS):
                raise PermissionError(f"git: rejected ref substring in {args.ref!r}")

        if args.op == "status":
            argv = [git_binary, "-C", str(repo), "status", "--porcelain=v1", "-z"]
            stdout = await _run_git(argv, timeout)
            entries = _parse_status(stdout, max_status)
            return GitResponse(op="status", repo=str(repo), status=entries, truncated=len(entries) >= max_status)

        if args.op == "log":
            limit = min(args.limit or max_log, max_log)
            fmt = "%H%x1f%an <%ae>%x1f%ai%x1f%s%x1e"
            argv = [
                git_binary, "-C", str(repo), "log",
                f"--pretty=format:{fmt}",
                f"--max-count={limit}",
            ]
            if args.since:
                argv += [f"--since={args.since}"]
            if args.until:
                argv += [f"--until={args.until}"]
            if args.ref:
                argv += [args.ref]
            if args.path:
                argv += ["--", args.path]
            stdout = await _run_git(argv, timeout)
            commits = _parse_log(stdout)
            return GitResponse(op="log", repo=str(repo), commits=commits)

        if args.op == "diff":
            argv = [git_binary, "-C", str(repo), "diff", "--no-color"]
            if args.ref:
                argv += [args.ref]
            if args.path:
                argv += ["--", args.path]
            stdout = await _run_git(argv, timeout)
            truncated = False
            if len(stdout.encode("utf-8")) > max_diff_bytes:
                stdout = stdout[:max_diff_bytes]
                truncated = True
            return GitResponse(op="diff", repo=str(repo), diff=stdout, truncated=truncated)

        if args.op == "show":
            argv = [git_binary, "-C", str(repo), "show", "--no-color"]
            if args.ref:
                argv += [args.ref]
            stdout = await _run_git(argv, timeout)
            return GitResponse(op="show", repo=str(repo), diff=stdout)

        if args.op == "branch":
            argv = [git_binary, "-C", str(repo), "branch", "--list", "--no-color"]
            stdout = await _run_git(argv, timeout)
            branches = []
            current = None
            for line in stdout.splitlines():
                line = line.strip()
                if line.startswith("* "):
                    current = line[2:]
                    branches.append(current)
                elif line:
                    branches.append(line)
            return GitResponse(op="branch", repo=str(repo), branches=branches, current_branch=current)

        if args.op == "add":
            if not args.path:
                raise PermissionError("git.add requires `path`")
            argv = [git_binary, "-C", str(repo), "add", "--", args.path]
            stdout = await _run_git(argv, timeout)
            return GitResponse(op="add", repo=str(repo), stdout=stdout)

        if args.op == "commit":
            if not args.message:
                raise PermissionError("git.commit requires `message`")
            argv = [git_binary, "-C", str(repo), "commit", "-m", args.message]
            stdout = await _run_git(argv, timeout)
            return GitResponse(op="commit", repo=str(repo), stdout=stdout)

        raise PermissionError(f"git: unsupported op {args.op!r}")


async def _run_git(argv: list[str], timeout: int) -> str:
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError as exc:
        proc.kill()
        await proc.wait()
        raise PermissionError(f"git: {argv[0]} timed out after {timeout}s") from exc
    if proc.returncode != 0:
        message = stderr_b.decode("utf-8", errors="replace").strip()
        raise PermissionError(f"git: {' '.join(argv[:4])} failed: {message}")
    return stdout_b.decode("utf-8", errors="replace")


def _parse_status(stdout: str, limit: int) -> list[GitStatusEntry]:
    # status --porcelain=v1 -z separates entries by NUL.
    entries: list[GitStatusEntry] = []
    parts = [p for p in stdout.split("\x00") if p]
    for part in parts:
        if len(part) < 3:
            continue
        index_char = part[0]
        work_char = part[1]
        path = part[3:]
        entries.append(GitStatusEntry(path=path, index=index_char, worktree=work_char))
        if len(entries) >= limit:
            break
    return entries


def _parse_log(stdout: str) -> list[GitCommit]:
    commits: list[GitCommit] = []
    for record in stdout.split("\x1e"):
        record = record.strip()
        if not record:
            continue
        fields = record.split("\x1f")
        if len(fields) < 4:
            continue
        sha, author, date, subject = fields[:4]
        commits.append(GitCommit(sha=sha, author=author, date=date, subject=subject))
    return commits


def _is_within(target: Path, base: Path) -> bool:
    try:
        target.relative_to(base)
    except ValueError:
        return target == base
    return True
