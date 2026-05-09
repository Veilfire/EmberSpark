"""Spark daemonization subsystem.

Three deployment modes, all YAML-driven:

- **naked**       — venv + systemd (Linux) / launchd (macOS). Lightest,
                    lowest isolation.
- **docker**      — container with the state directory mounted as a volume.
                    Medium isolation, Windows-friendly, good for servers.
- **firecracker** — microVM with hardware-backed isolation. Strongest,
                    Linux-only, KVM required.

All three install/start/stop via the same ``spark daemon`` CLI and are
configured from the ``spec.daemon`` block of ``~/.spark/spark.yaml``.
"""

from __future__ import annotations

from spark.daemon.install import DaemonResult, daemon_control, install_daemon, uninstall_daemon

__all__ = [
    "DaemonResult",
    "daemon_control",
    "install_daemon",
    "uninstall_daemon",
]
