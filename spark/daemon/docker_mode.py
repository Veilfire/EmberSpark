"""Docker daemon mode: container orchestration via `docker compose`."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Literal

from spark.config.runtime_config import DaemonDockerConfig, SparkRuntime
from spark.daemon.install import DaemonResult

DOCKER_DIR = Path(__file__).parent / "docker"
COMPOSE_FILE = DOCKER_DIR / "docker-compose.yml"
DOCKERFILE = DOCKER_DIR / "Dockerfile"


def _compose_cmd() -> list[str] | None:
    # Prefer `docker compose`; fall back to `docker-compose`.
    if shutil.which("docker") is not None:
        return ["docker", "compose"]
    if shutil.which("docker-compose") is not None:
        return ["docker-compose"]
    return None


def _compose_env(daemon: DaemonDockerConfig, config: SparkRuntime | None = None) -> dict[str, str]:
    import os as _os

    env = dict(_os.environ)
    env.update(
        {
            "SPARK_CONTAINER_NAME": daemon.container_name,
            "SPARK_CPUS": str(daemon.cpus),
            "SPARK_MEMORY": f"{daemon.memory_mb}m",
            "SPARK_PORT": "7777",
        }
    )
    # Bind-mount paths for the compose volumes. Defaults point at
    # ~/.spark and ~/.spark/data on the host; operators can override
    # both via runtime env vars before calling `spark daemon install`.
    host_state_dir = _os.environ.get("SPARK_HOST_STATE_DIR")
    host_data_dir = _os.environ.get("SPARK_HOST_DATA_DIR")
    if host_state_dir is None:
        host_state_dir = str(Path("~/.spark").expanduser())
    if host_data_dir is None:
        if config is not None and config.spec.data_volume.enabled:
            host_data_dir = str(config.spec.data_volume.root_path)
        else:
            host_data_dir = str(Path("~/.spark/data").expanduser())
    env["SPARK_HOST_STATE_DIR"] = host_state_dir
    env["SPARK_HOST_DATA_DIR"] = host_data_dir
    return env


def install(
    config: SparkRuntime,
    daemon: DaemonDockerConfig,
    *,
    dry_run: bool,
) -> DaemonResult:
    result = DaemonResult()
    if not COMPOSE_FILE.exists() or not DOCKERFILE.exists():
        result.ok = False
        result.add(f"missing {COMPOSE_FILE} or {DOCKERFILE}")
        return result
    result.add(f"compose file: {COMPOSE_FILE}")
    result.add(f"dockerfile:   {DOCKERFILE}")
    result.add(f"image:        {daemon.image}")
    result.add(f"memory:       {daemon.memory_mb} MiB, cpus: {daemon.cpus}")

    cmd = _compose_cmd()
    if cmd is None:
        result.ok = False
        result.add("docker (or docker compose) not installed")
        return result
    if dry_run:
        result.add(f"[dry-run] would run: {' '.join(cmd)} -f {COMPOSE_FILE} build")
        result.add(f"[dry-run] would run: {' '.join(cmd)} -f {COMPOSE_FILE} up -d")
        return result

    _run(result, [*cmd, "-f", str(COMPOSE_FILE), "build"], env=_compose_env(daemon, config))
    if result.ok:
        _run(result, [*cmd, "-f", str(COMPOSE_FILE), "up", "-d"], env=_compose_env(daemon, config))
    return result


def uninstall(config: SparkRuntime, daemon: DaemonDockerConfig) -> DaemonResult:
    result = DaemonResult()
    cmd = _compose_cmd()
    if cmd is None:
        result.ok = False
        result.add("docker not installed")
        return result
    _run(
        result,
        [*cmd, "-f", str(COMPOSE_FILE), "down", "--volumes"],
        env=_compose_env(daemon, config),
    )
    return result


def control(
    config: SparkRuntime,
    daemon: DaemonDockerConfig,
    action: Literal["start", "stop", "status"],
) -> DaemonResult:
    result = DaemonResult()
    cmd = _compose_cmd()
    if cmd is None:
        result.ok = False
        result.add("docker not installed")
        return result
    env = _compose_env(daemon, config)
    if action == "start":
        _run(result, [*cmd, "-f", str(COMPOSE_FILE), "start"], env=env)
    elif action == "stop":
        _run(result, [*cmd, "-f", str(COMPOSE_FILE), "stop"], env=env)
    elif action == "status":
        _run(result, [*cmd, "-f", str(COMPOSE_FILE), "ps"], env=env)
    return result


def _run(result: DaemonResult, argv: list[str], *, env: dict[str, str] | None = None) -> None:
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, check=False, env=env)
    except FileNotFoundError as exc:
        result.ok = False
        result.add(f"{argv[0]}: {exc}")
        return
    if proc.stdout:
        result.add(proc.stdout.strip())
    if proc.stderr:
        result.add(proc.stderr.strip())
    if proc.returncode != 0:
        result.ok = False
        result.add(f"{argv[0]} exited {proc.returncode}")
