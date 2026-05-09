"""Sandbox policy — built from agent permissions, not plugin-controlled."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import structlog

from spark.config.enums import Permission, SandboxBackend
from spark.config.models import Permissions

log = structlog.get_logger("spark.sandbox.policy")


@dataclass(frozen=True)
class Rlimits:
    cpu_seconds: int
    memory_mb: int
    max_open_files: int
    max_processes: int


@dataclass(frozen=True)
class SandboxPolicy:
    ro_paths: tuple[Path, ...]
    rw_paths: tuple[Path, ...]
    allow_network: bool
    allow_hosts: tuple[str, ...]
    env: dict[str, str] = field(default_factory=dict)
    rlimits: Rlimits = field(default_factory=lambda: Rlimits(30, 512, 128, 8))
    timeout_seconds: int = 60
    backend: SandboxBackend = SandboxBackend.AUTO
    enabled: bool = True


def _chroma_collision(candidate: Path, chroma: Path) -> bool:
    """True if ``candidate`` collides with the runtime's Chroma directory.

    Rejects any of:

    - ``candidate`` equals ``chroma`` (exact match)
    - ``candidate`` is an ancestor of ``chroma`` (a broader grant that would
      let a plugin traverse into chroma)
    - ``candidate`` is inside ``chroma`` (a targeted grant for a subdirectory
      of the Chroma store)

    Belt + suspenders: none of these are ever legitimate for a tool plugin.
    Chroma is runtime-private.
    """
    try:
        candidate = candidate.expanduser().resolve()
    except (OSError, RuntimeError):
        return False
    try:
        if candidate == chroma:
            return True
        # candidate is an ancestor of chroma
        chroma.relative_to(candidate)
        return True
    except ValueError:
        pass
    try:
        # candidate is inside chroma
        candidate.relative_to(chroma)
        return True
    except ValueError:
        return False


def build_policy(
    permissions: Permissions,
    *,
    extra_ro_paths: list[Path] | None = None,
    allow_network: bool = False,
) -> SandboxPolicy:
    """Derive a SandboxPolicy from an Agent's Permissions.

    `allow_network` is tri-state: we only allow network if a network-needing
    plugin asked for it *and* the agent has network allowlisted hosts.

    If a process-scoped data volume is active, the volume's ``scratch`` and
    ``deliverables`` subdirectories are **automatically** appended to
    ``rw_paths`` for agents that hold the ``fs.write`` grant. The volume's
    ``chroma`` subdirectory is **never** included, even if it (or any of its
    ancestors) appears in the agent's declared ``filesystem.allow_paths``.
    """
    # Local import — runtime_config is pure, but this module is imported
    # early by the tool runtime.
    from spark.config.runtime_config import get_data_volume

    sandbox = permissions.sandbox
    rw_paths: list[Path] = []
    ro_paths: list[Path] = list(extra_ro_paths or [])

    dv = get_data_volume()
    chroma_path: Path | None = dv.chroma_path if dv is not None else None

    for p in permissions.filesystem.allow_paths:
        candidate = Path(p).expanduser().resolve()
        if chroma_path is not None and _chroma_collision(candidate, chroma_path):
            log.warning(
                "sandbox.chroma_path_refused",
                event="security.sandbox.chroma_path_refused",
                refused_path=str(candidate),
                chroma_path=str(chroma_path),
                severity="elevated",
            )
            continue
        rw_paths.append(candidate)

    has_fs_write = Permission.FS_WRITE in permissions.grants
    if dv is not None and has_fs_write:
        # Auto-append the user-data subpaths so every plugin with fs.write
        # can use well-known locations (`ctx.scratch_path`,
        # `ctx.deliverables_path`) without the operator having to repeat
        # them in every agent YAML.
        for auto_path in (dv.scratch_path, dv.deliverables_path):
            auto_path = auto_path.expanduser().resolve()
            if auto_path not in rw_paths:
                rw_paths.append(auto_path)

    allow_hosts = tuple(permissions.network.allow_hosts)
    # Network kill-switch: only the ``net.http`` grant gates whether bwrap
    # shares the parent netns. The agent's ``allow_hosts`` is *advisory* —
    # plugins enforce per-call hostname validation through their own
    # HostPolicy (see spark.utils.net), so an empty operator-level list no
    # longer silently disables networking for plugins that legitimately
    # need it. If the operator wants a hard hostname allowlist, they list
    # hosts here AND the per-plugin policies pick them up; if they don't,
    # plugin-level defenses are still in effect.
    has_net_grant = Permission.NET_HTTP in permissions.grants
    net_ok = allow_network and has_net_grant
    if allow_network and not has_net_grant:
        log.warning(
            "sandbox.network_grant_missing",
            event="security.sandbox.network_grant_missing",
            severity="elevated",
        )

    return SandboxPolicy(
        ro_paths=tuple(ro_paths),
        rw_paths=tuple(rw_paths),
        allow_network=net_ok,
        allow_hosts=allow_hosts,
        rlimits=Rlimits(
            cpu_seconds=sandbox.cpu_seconds,
            memory_mb=sandbox.memory_mb,
            max_open_files=sandbox.max_open_files,
            max_processes=sandbox.max_processes,
        ),
        timeout_seconds=sandbox.timeout_seconds,
        backend=sandbox.backend,
        enabled=sandbox.enabled,
    )
