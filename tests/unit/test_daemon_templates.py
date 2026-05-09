"""Tests for daemon template rendering (no actual install)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from spark.config.runtime_config import (
    DaemonDockerConfig,
    DaemonFirecrackerConfig,
    DaemonNakedConfig,
    SparkRuntime,
    SparkRuntimeSpec,
    WebBindLoopback,
    WebConfig,
)
from spark.daemon import install_daemon


def _runtime(daemon) -> SparkRuntime:
    return SparkRuntime(
        spec=SparkRuntimeSpec(
            web=WebConfig(enabled=True, bind=WebBindLoopback()),
            daemon=daemon,
        )
    )


def test_install_without_daemon_block() -> None:
    cfg = SparkRuntime(
        spec=SparkRuntimeSpec(web=WebConfig(enabled=True, bind=WebBindLoopback()))
    )
    result = install_daemon(cfg, dry_run=True)
    assert result.ok is False
    assert any("spec.daemon" in line for line in result.lines)


def test_naked_dry_run_renders_template(tmp_path: Path) -> None:
    daemon = DaemonNakedConfig(log_dir=tmp_path / "logs")
    cfg = _runtime(daemon)
    with patch("spark.daemon.naked._is_linux", return_value=True), patch(
        "spark.daemon.naked._is_macos", return_value=False
    ):
        result = install_daemon(cfg, dry_run=True)
    assert result.ok
    joined = "\n".join(result.lines)
    assert "[dry-run]" in joined
    assert "ExecStart=" in joined
    assert "NoNewPrivileges=true" in joined
    assert "ProtectSystem=strict" in joined


def test_docker_dry_run(tmp_path: Path) -> None:
    daemon = DaemonDockerConfig(container_name="spark-test")
    cfg = _runtime(daemon)
    with patch("spark.daemon.docker_mode._compose_cmd", return_value=["docker", "compose"]):
        result = install_daemon(cfg, dry_run=True)
    assert result.ok
    joined = "\n".join(result.lines)
    assert "[dry-run]" in joined
    assert "docker compose" in joined


def test_firecracker_vmconfig_rendered(tmp_path: Path) -> None:
    daemon = DaemonFirecrackerConfig(
        rootfs=tmp_path / "rootfs.ext4",
        kernel=tmp_path / "vmlinux",
        vcpus=2,
        memory_mib=512,
    )
    # Pre-create empty files so the existence check passes.
    daemon.rootfs.expanduser().parent.mkdir(parents=True, exist_ok=True)
    daemon.rootfs.expanduser().write_bytes(b"")
    daemon.kernel.expanduser().write_bytes(b"")

    from spark.daemon.firecracker_mode import _render_vmconfig

    out = tmp_path / "vmconfig.json"
    _render_vmconfig(daemon, out)
    parsed = json.loads(out.read_text())
    assert parsed["machine-config"]["vcpu_count"] == 2
    assert parsed["machine-config"]["mem_size_mib"] == 512
    assert parsed["drives"][0]["is_root_device"] is True
    assert parsed["drives"][0]["path_on_host"] == str(daemon.rootfs.expanduser())
    assert parsed["vsock"]["guest_cid"] == daemon.vsock_cid
