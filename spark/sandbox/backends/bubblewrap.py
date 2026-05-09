"""Bubblewrap sandbox backend (Linux default)."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

from spark.sandbox.policy import SandboxPolicy


def _is_containerized() -> bool:
    """Detect whether we're running inside Docker / Podman / etc.

    The kernel restricts a few sandbox primitives — fresh procfs mounts,
    fresh devtmpfs — when those operations happen inside a *nested* user
    namespace (the container's userns + bwrap's ``--unshare-user``).
    bwrap aborts with ``Can't mount proc on /newroot/proc: Permission
    denied`` in that scenario.

    The standard markers:

    - ``/run/.containerenv`` is dropped by Podman.
    - ``/.dockerenv`` is dropped by Docker.

    Either tells us we should swap a few mounts for bind-mounts that the
    nested userns kernel allows. The trade-off is small: the worker
    process can read ``/proc`` for processes in the container's PID
    namespace (already isolated from the host), but cannot write — its
    privilege boundary is preserved by the user-namespace gate.
    """
    return os.path.exists("/run/.containerenv") or os.path.exists("/.dockerenv")


class BubblewrapBackend:
    name = "bubblewrap"

    def available(self) -> bool:
        return sys.platform.startswith("linux") and shutil.which("bwrap") is not None

    def build_argv(self, worker_argv: list[str], policy: SandboxPolicy) -> list[str]:
        """Build the bwrap argv that wraps `worker_argv`.

        Everything outside `ro_paths` / `rw_paths` / essential system libs is
        inaccessible. When `allow_network` is False we unshare the network
        namespace entirely.

        Two argv shapes:

        - **Host mode** (default — direct Linux deployments): full
          ``--unshare-pid`` + fresh ``/proc`` and ``/dev`` mounts.
          Strongest isolation; the worker can only see itself in
          ``/proc`` and gets a synthetic ``/dev`` containing only the
          handful of nodes bwrap creates.

        - **Containerized mode** (``/run/.containerenv`` or
          ``/.dockerenv`` present): drop ``--unshare-pid``, bind-mount
          ``/proc`` read-only, bind-mount ``/dev``. Keeps every other
          isolation gate (user namespace, UTS, IPC, cgroup, optional
          net) — only the procfs + devtmpfs operations that the kernel
          refuses inside a nested userns are downgraded to bind-mounts.
        """
        containerized = _is_containerized()
        argv: list[str] = [
            "bwrap",
            "--die-with-parent",
            "--new-session",
            "--unshare-user",
            "--unshare-uts",
            "--unshare-cgroup-try",
            "--unshare-ipc",
        ]
        if not containerized:
            argv += [
                "--unshare-pid",
                "--proc", "/proc",
                "--dev", "/dev",
            ]
        else:
            argv += [
                "--ro-bind", "/proc", "/proc",
                "--dev-bind", "/dev", "/dev",
            ]
        argv += [
            "--tmpfs", "/tmp",  # noqa: S108 — tmpfs only, not a shared /tmp
            "--ro-bind", "/usr", "/usr",
            "--symlink", "usr/lib", "/lib",
            "--symlink", "usr/lib64", "/lib64",
            "--symlink", "usr/bin", "/bin",
            "--symlink", "usr/sbin", "/sbin",
            "--chdir", "/",
        ]

        # Python runtime
        python_prefix = Path(sys.prefix).resolve()
        argv += ["--ro-bind", str(python_prefix), str(python_prefix)]
        # Site-packages may live outside prefix (venv); resolve and bind each.
        for site in _site_dirs():
            argv += ["--ro-bind", site, site]

        for p in policy.ro_paths:
            argv += ["--ro-bind-try", str(p), str(p)]
        for p in policy.rw_paths:
            argv += ["--bind-try", str(p), str(p)]

        if not policy.allow_network:
            argv += ["--unshare-net"]
        else:
            # Network plugins need glibc's resolver to find a nameserver and
            # OpenSSL/httpx to find a CA bundle. Bind /etc read-only.
            #
            # Individual --ro-bind /etc/resolv.conf would be tighter, but
            # podman publishes resolv.conf/hosts/hostname as their own tmpfs
            # mounts inside /etc, and bind-mounting a single file under a
            # nested user namespace fails with "Unable to remount destination
            # ... with correct flags: Permission denied". The whole-/etc
            # bind avoids the per-file remount entirely and works in both
            # host and containerized modes.
            argv += ["--ro-bind-try", "/etc", "/etc"]

        # Tight env: only what the worker needs.
        argv += [
            "--clearenv",
            "--setenv", "PATH", "/usr/bin:/usr/local/bin",
            "--setenv", "PYTHONUNBUFFERED", "1",
            "--setenv", "PYTHONHASHSEED", "random",
            "--setenv", "LC_ALL", "C.UTF-8",
            "--setenv", "HOME", "/tmp",  # noqa: S108 — tmpfs
        ]
        for k, v in policy.env.items():
            argv += ["--setenv", k, v]

        argv += ["--"]
        argv += worker_argv
        return argv


def _site_dirs() -> list[str]:
    import site

    dirs: set[str] = set()
    try:
        for d in site.getsitepackages():
            dirs.add(str(Path(d).resolve()))
    except Exception:  # pragma: no cover
        pass
    user_site = site.getusersitepackages()
    if user_site:
        dirs.add(str(Path(user_site).resolve()))
    return sorted(dirs)
