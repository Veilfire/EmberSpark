"""Top-level daemon install/uninstall/control dispatcher."""

from __future__ import annotations

from dataclasses import dataclass, field

from spark.config.runtime_config import (
    DaemonDockerConfig,
    DaemonFirecrackerConfig,
    DaemonNakedConfig,
    SparkRuntime,
)


@dataclass
class DaemonResult:
    ok: bool = True
    lines: list[str] = field(default_factory=list)

    def add(self, line: str) -> None:
        self.lines.append(line)


def install_daemon(config: SparkRuntime, *, dry_run: bool = False) -> DaemonResult:
    daemon = config.spec.daemon
    if daemon is None:
        return DaemonResult(ok=False, lines=["no spec.daemon block in config"])
    if isinstance(daemon, DaemonNakedConfig):
        from spark.daemon.naked import install as install_naked

        return install_naked(config, daemon, dry_run=dry_run)
    if isinstance(daemon, DaemonDockerConfig):
        from spark.daemon.docker_mode import install as install_docker

        return install_docker(config, daemon, dry_run=dry_run)
    if isinstance(daemon, DaemonFirecrackerConfig):
        from spark.daemon.firecracker_mode import install as install_fc

        return install_fc(config, daemon, dry_run=dry_run)
    return DaemonResult(ok=False, lines=[f"unknown daemon mode: {type(daemon).__name__}"])


def uninstall_daemon(config: SparkRuntime) -> DaemonResult:
    daemon = config.spec.daemon
    if daemon is None:
        return DaemonResult(ok=False, lines=["no spec.daemon block in config"])
    if isinstance(daemon, DaemonNakedConfig):
        from spark.daemon.naked import uninstall

        return uninstall(config, daemon)
    if isinstance(daemon, DaemonDockerConfig):
        from spark.daemon.docker_mode import uninstall

        return uninstall(config, daemon)
    if isinstance(daemon, DaemonFirecrackerConfig):
        from spark.daemon.firecracker_mode import uninstall

        return uninstall(config, daemon)
    return DaemonResult(ok=False, lines=[f"unknown daemon mode"])


def daemon_control(config: SparkRuntime, action: str) -> DaemonResult:
    daemon = config.spec.daemon
    if daemon is None:
        return DaemonResult(ok=False, lines=["no spec.daemon block in config"])
    if isinstance(daemon, DaemonNakedConfig):
        from spark.daemon.naked import control

        return control(config, daemon, action)
    if isinstance(daemon, DaemonDockerConfig):
        from spark.daemon.docker_mode import control

        return control(config, daemon, action)
    if isinstance(daemon, DaemonFirecrackerConfig):
        from spark.daemon.firecracker_mode import control

        return control(config, daemon, action)
    return DaemonResult(ok=False, lines=["unknown daemon mode"])
