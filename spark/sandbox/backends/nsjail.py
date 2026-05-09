"""nsjail sandbox backend — stricter Linux option, opt-in per agent."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

from spark.sandbox.policy import SandboxPolicy


class NsjailBackend:
    name = "nsjail"

    def available(self) -> bool:
        return sys.platform.startswith("linux") and shutil.which("nsjail") is not None

    def build_argv(self, worker_argv: list[str], policy: SandboxPolicy) -> list[str]:
        argv: list[str] = [
            "nsjail",
            "--mode", "o",       # once
            "--quiet",
            "--disable_clone_newuser",  # rely on ambient user ns
            "--rlimit_as", str(policy.rlimits.memory_mb),
            "--rlimit_cpu", str(policy.rlimits.cpu_seconds),
            "--rlimit_fsize", "64",
            "--rlimit_nofile", str(policy.rlimits.max_open_files),
            "--rlimit_nproc", str(policy.rlimits.max_processes),
            "--time_limit", str(policy.timeout_seconds),
            "--cwd", "/",
            "--bindmount_ro", "/usr:/usr",
            "--bindmount_ro", "/lib:/lib",
            "--bindmount_ro", "/lib64:/lib64",
            "--bindmount_ro", "/bin:/bin",
            "--bindmount_ro", f"{sys.prefix}:{sys.prefix}",
            "--tmpfsmount", "/tmp",
            "--mount", "none:/proc:proc:ro=true",
        ]
        for p in policy.ro_paths:
            argv += ["--bindmount_ro", f"{Path(p).resolve()}:{Path(p).resolve()}"]
        for p in policy.rw_paths:
            argv += ["--bindmount", f"{Path(p).resolve()}:{Path(p).resolve()}"]
        if not policy.allow_network:
            argv += ["--disable_proc", "--iface_no_lo"]
        argv += ["--env", "PATH=/usr/bin:/usr/local/bin"]
        argv += ["--", *worker_argv]
        return argv
