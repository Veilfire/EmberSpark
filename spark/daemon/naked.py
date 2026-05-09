"""Naked daemon mode: venv + systemd (Linux) or launchd (macOS).

**Trade-offs.** This mode runs Spark directly on the host, inside a Python
venv. There is no additional isolation beyond what Spark's own sandbox
provides. Use this mode only on a machine you fully trust.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Literal

from spark.config.runtime_config import DaemonNakedConfig, SparkRuntime
from spark.daemon.install import DaemonResult

SYSTEMD_TEMPLATE = """\
[Unit]
Description=Spark agent runtime ({service_name})
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart={python} -m spark.cli.main serve --config {config}
Restart={restart}
RestartSec=5
StandardOutput=append:{log_dir}/spark.stdout.log
StandardError=append:{log_dir}/spark.stderr.log
Environment=PYTHONUNBUFFERED=1
Environment=PYTHONHASHSEED=random
Environment=HOME={home}

# Tightening — in addition to Spark's own sandbox layer.
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ReadWritePaths={state_dir}
ProtectHome=read-only
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictRealtime=true
RestrictSUIDSGID=true
LockPersonality=true
MemoryDenyWriteExecute=true
SystemCallArchitectures=native

[Install]
WantedBy=default.target
"""

LAUNCHD_TEMPLATE = """\
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
  <dict>
    <key>Label</key><string>dev.veilfire.spark</string>
    <key>ProgramArguments</key>
    <array>
      <string>{python}</string>
      <string>-m</string>
      <string>spark.cli.main</string>
      <string>serve</string>
      <string>--config</string>
      <string>{config}</string>
    </array>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key>
    <dict><key>SuccessfulExit</key><false/></dict>
    <key>StandardOutPath</key><string>{log_dir}/spark.stdout.log</string>
    <key>StandardErrorPath</key><string>{log_dir}/spark.stderr.log</string>
    <key>EnvironmentVariables</key>
    <dict>
      <key>PYTHONUNBUFFERED</key><string>1</string>
      <key>HOME</key><string>{home}</string>
    </dict>
    <key>ProcessType</key><string>Interactive</string>
  </dict>
</plist>
"""


def _is_linux() -> bool:
    return sys.platform.startswith("linux")


def _is_macos() -> bool:
    return sys.platform == "darwin"


def _systemd_unit_path(service_name: str) -> Path:
    return Path.home() / ".config" / "systemd" / "user" / f"{service_name}.service"


def _launchd_plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / "dev.veilfire.spark.plist"


def _render_systemd(config: SparkRuntime, daemon: DaemonNakedConfig) -> str:
    home = str(Path.home())
    log_dir = daemon.log_dir.expanduser()
    log_dir.mkdir(parents=True, exist_ok=True)
    state_dir = str((Path.home() / ".spark").resolve())
    return SYSTEMD_TEMPLATE.format(
        service_name=daemon.service_name,
        python=sys.executable,
        config=str(Path("~/.spark/spark.yaml").expanduser()),
        restart="on-failure" if daemon.restart_on_failure else "no",
        log_dir=str(log_dir),
        home=home,
        state_dir=state_dir,
    )


def _render_launchd(config: SparkRuntime, daemon: DaemonNakedConfig) -> str:
    home = str(Path.home())
    log_dir = daemon.log_dir.expanduser()
    log_dir.mkdir(parents=True, exist_ok=True)
    return LAUNCHD_TEMPLATE.format(
        python=sys.executable,
        config=str(Path("~/.spark/spark.yaml").expanduser()),
        log_dir=str(log_dir),
        home=home,
    )


def install(
    config: SparkRuntime,
    daemon: DaemonNakedConfig,
    *,
    dry_run: bool,
) -> DaemonResult:
    result = DaemonResult()
    if _is_linux():
        unit_path = _systemd_unit_path(daemon.service_name)
        content = _render_systemd(config, daemon)
        if dry_run:
            result.add(f"[dry-run] would write {unit_path}")
            result.add(content)
            return result
        unit_path.parent.mkdir(parents=True, exist_ok=True)
        unit_path.write_text(content)
        result.add(f"wrote {unit_path}")
        _run(result, ["systemctl", "--user", "daemon-reload"])
        _run(result, ["systemctl", "--user", "enable", "--now", daemon.service_name])
    elif _is_macos():
        plist_path = _launchd_plist_path()
        content = _render_launchd(config, daemon)
        if dry_run:
            result.add(f"[dry-run] would write {plist_path}")
            result.add(content)
            return result
        plist_path.parent.mkdir(parents=True, exist_ok=True)
        plist_path.write_text(content)
        result.add(f"wrote {plist_path}")
        _run(result, ["launchctl", "load", "-w", str(plist_path)])
    else:
        result.ok = False
        result.add(f"unsupported platform: {sys.platform}")
    return result


def uninstall(config: SparkRuntime, daemon: DaemonNakedConfig) -> DaemonResult:
    result = DaemonResult()
    if _is_linux():
        _run(result, ["systemctl", "--user", "disable", "--now", daemon.service_name])
        unit_path = _systemd_unit_path(daemon.service_name)
        if unit_path.exists():
            unit_path.unlink()
            result.add(f"removed {unit_path}")
        _run(result, ["systemctl", "--user", "daemon-reload"])
    elif _is_macos():
        plist_path = _launchd_plist_path()
        if plist_path.exists():
            _run(result, ["launchctl", "unload", str(plist_path)])
            plist_path.unlink()
            result.add(f"removed {plist_path}")
    else:
        result.ok = False
        result.add(f"unsupported platform: {sys.platform}")
    return result


def control(
    config: SparkRuntime,
    daemon: DaemonNakedConfig,
    action: Literal["start", "stop", "status"],
) -> DaemonResult:
    result = DaemonResult()
    if _is_linux():
        if action == "status":
            _run(result, ["systemctl", "--user", "status", daemon.service_name], ok_rcs={0, 3})
        else:
            _run(result, ["systemctl", "--user", action, daemon.service_name])
    elif _is_macos():
        plist_path = _launchd_plist_path()
        if action == "start":
            _run(result, ["launchctl", "start", "dev.veilfire.spark"])
        elif action == "stop":
            _run(result, ["launchctl", "stop", "dev.veilfire.spark"])
        elif action == "status":
            _run(result, ["launchctl", "list", "dev.veilfire.spark"], ok_rcs={0, 113})
    else:
        result.ok = False
        result.add(f"unsupported platform: {sys.platform}")
    return result


def _run(
    result: DaemonResult,
    argv: list[str],
    ok_rcs: set[int] | None = None,
) -> None:
    ok_rcs = ok_rcs or {0}
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, check=False)
    except FileNotFoundError as exc:
        result.ok = False
        result.add(f"{argv[0]}: {exc}")
        return
    stdout = (proc.stdout or "").strip()
    stderr = (proc.stderr or "").strip()
    if stdout:
        result.add(stdout)
    if stderr:
        result.add(stderr)
    if proc.returncode not in ok_rcs:
        result.ok = False
        result.add(f"{argv[0]} exited {proc.returncode}")
