"""Firecracker microVM daemon mode.

This module orchestrates Spark as a Firecracker microVM. It is a lot of
surface for rarely-needed isolation, so we keep it focused on:

- rendering the VM config JSON from the `DaemonFirecrackerConfig`;
- generating an optional systemd unit (``spark-firecracker.service``) that
  invokes ``launcher.sh``;
- providing install / uninstall / start / stop wrappers.

The rootfs build is a separate script (``build-rootfs.sh``) because it needs
root and runs debootstrap.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Literal

from spark.config.runtime_config import DaemonFirecrackerConfig, SparkRuntime
from spark.daemon.install import DaemonResult

FC_DIR = Path(__file__).parent / "firecracker"
VMCONFIG_TEMPLATE = FC_DIR / "vmconfig.template.json"
LAUNCHER = FC_DIR / "launcher.sh"
ROOTFS_BUILDER = FC_DIR / "build-rootfs.sh"

SERVICE_UNIT_TEMPLATE = """\
[Unit]
Description=Spark Firecracker microVM
Wants=network-online.target
After=network-online.target

[Service]
Type=simple
Environment=CONFIG_PATH={vmconfig}
Environment=TAP={tap}
Environment=HOST_CIDR={host_cidr}
Environment=GUEST_IP={guest_ip}
Environment=FORWARD_PORTS={forwards}
Environment=FIRECRACKER={firecracker}
ExecStart={launcher}
Restart=on-failure
RestartSec=5

# This service needs root (TAP + iptables). Install as a system unit.
[Install]
WantedBy=multi-user.target
"""


def _host_from_cidr(cidr: str) -> str:
    return cidr.split("/")[0]


def _render_vmconfig(daemon: DaemonFirecrackerConfig, out_path: Path) -> None:
    template = VMCONFIG_TEMPLATE.read_text()
    rendered = (
        template.replace("{{ kernel }}", str(daemon.kernel.expanduser()))
        .replace("{{ rootfs }}", str(daemon.rootfs.expanduser()))
        .replace("{{ data_image }}", str(daemon.data_image.expanduser()))
        .replace("{{ vcpus }}", str(daemon.vcpus))
        .replace("{{ memory_mib }}", str(daemon.memory_mib))
        .replace("{{ tap }}", daemon.tap_device)
        .replace("{{ vsock_cid }}", str(daemon.vsock_cid))
        .replace("{{ vsock_path }}", "/tmp/spark-vsock.sock")  # noqa: S108
        .replace("{{ guest_ip }}", daemon.guest_ip)
        .replace("{{ gateway }}", _host_from_cidr(daemon.host_cidr))
    )
    # Validate it's still JSON.
    json.loads(rendered)
    out_path.write_text(rendered)


def _forward_ports_env(daemon: DaemonFirecrackerConfig) -> str:
    return ",".join(f"{h}:{g}" for h, g in daemon.forwarded_ports.items())


def _unit_path() -> Path:
    return Path("/etc/systemd/system/spark-firecracker.service")


def install(
    config: SparkRuntime,
    daemon: DaemonFirecrackerConfig,
    *,
    dry_run: bool,
) -> DaemonResult:
    result = DaemonResult()
    if not sys.platform.startswith("linux"):
        result.ok = False
        result.add("firecracker mode is Linux-only")
        return result

    # Check prerequisites.
    if not daemon.firecracker_binary.exists() and shutil.which("firecracker") is None:
        result.ok = False
        result.add(
            f"firecracker binary not found at {daemon.firecracker_binary}. "
            "Install from https://github.com/firecracker-microvm/firecracker/releases"
        )
        return result
    if not daemon.rootfs.expanduser().exists():
        result.add(
            f"[warn] rootfs missing at {daemon.rootfs}; run "
            f"`sudo {ROOTFS_BUILDER}` first"
        )
    if not daemon.kernel.expanduser().exists():
        result.add(
            f"[warn] kernel missing at {daemon.kernel}; download an uncompressed "
            "vmlinux matching your Firecracker version."
        )
    if not daemon.data_image.expanduser().exists():
        result.add(
            f"[warn] data image missing at {daemon.data_image}; "
            f"re-run `sudo {ROOTFS_BUILDER}` with SPARK_DATA_IMAGE_SIZE_MIB="
            f"{daemon.data_image_size_mib} (or create the file manually with "
            "`truncate` + `mkfs.ext4`)"
        )

    vm_dir = daemon.rootfs.expanduser().parent
    vm_dir.mkdir(parents=True, exist_ok=True)
    vmconfig_path = vm_dir / "vmconfig.json"

    if dry_run:
        result.add(f"[dry-run] would render {vmconfig_path}")
        result.add(f"[dry-run] would write {_unit_path()}")
        return result

    _render_vmconfig(daemon, vmconfig_path)
    result.add(f"wrote {vmconfig_path}")

    unit = SERVICE_UNIT_TEMPLATE.format(
        vmconfig=str(vmconfig_path),
        tap=daemon.tap_device,
        host_cidr=daemon.host_cidr,
        guest_ip=daemon.guest_ip,
        forwards=_forward_ports_env(daemon),
        firecracker=str(daemon.firecracker_binary),
        launcher=str(LAUNCHER),
    )
    path = _unit_path()
    try:
        path.write_text(unit)
    except PermissionError:
        result.ok = False
        result.add(f"permission denied writing {path}; run with sudo")
        return result
    result.add(f"wrote {path}")
    _run(result, ["systemctl", "daemon-reload"])
    _run(result, ["systemctl", "enable", "spark-firecracker"])
    return result


def uninstall(config: SparkRuntime, daemon: DaemonFirecrackerConfig) -> DaemonResult:
    result = DaemonResult()
    if not sys.platform.startswith("linux"):
        result.ok = False
        result.add("firecracker mode is Linux-only")
        return result
    _run(result, ["systemctl", "disable", "--now", "spark-firecracker"])
    path = _unit_path()
    try:
        if path.exists():
            path.unlink()
            result.add(f"removed {path}")
    except PermissionError:
        result.ok = False
        result.add(f"permission denied removing {path}; run with sudo")
    _run(result, ["systemctl", "daemon-reload"])
    return result


def control(
    config: SparkRuntime,
    daemon: DaemonFirecrackerConfig,
    action: Literal["start", "stop", "status"],
) -> DaemonResult:
    result = DaemonResult()
    if not sys.platform.startswith("linux"):
        result.ok = False
        result.add("firecracker mode is Linux-only")
        return result
    if action == "status":
        _run(result, ["systemctl", "status", "spark-firecracker"], ok_rcs={0, 3})
    else:
        _run(result, ["systemctl", action, "spark-firecracker"])
    return result


def _run(
    result: DaemonResult,
    argv: list[str],
    *,
    ok_rcs: set[int] | None = None,
) -> None:
    ok_rcs = ok_rcs or {0}
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, check=False)
    except FileNotFoundError as exc:
        result.ok = False
        result.add(f"{argv[0]}: {exc}")
        return
    if proc.stdout:
        result.add(proc.stdout.strip())
    if proc.stderr:
        result.add(proc.stderr.strip())
    if proc.returncode not in ok_rcs:
        result.ok = False
        result.add(f"{argv[0]} exited {proc.returncode}")
