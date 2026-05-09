"""macOS Seatbelt (sandbox-exec) backend.

Generates an SBPL profile per call that denies everything by default and then
allows only the paths/hosts declared in `SandboxPolicy`. `sandbox-exec` is
deprecated by Apple but is still shipped and remains the only unprivileged
sandbox on macOS as of 2026.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

from spark.sandbox.policy import SandboxPolicy

_PROFILE_TEMPLATE = """(version 1)
(deny default)
(allow process*)
(allow signal (target self))
(allow sysctl-read)
(allow mach-lookup)
(allow ipc-posix-shm)
(allow file-read-metadata)
(allow file-read* (subpath "/usr"))
(allow file-read* (subpath "/System"))
(allow file-read* (subpath "/Library"))
(allow file-read* (subpath "/private/var/db"))
(allow file-read* (subpath "/private/etc"))
(allow file-read* (subpath "{python_prefix}"))
{ro_rules}
{rw_rules}
(allow file-read* (subpath "/private/tmp"))
(allow file-write* (subpath "/private/tmp"))
{network_rule}
"""


class SeatbeltBackend:
    name = "seatbelt"

    def available(self) -> bool:
        return sys.platform == "darwin" and shutil.which("sandbox-exec") is not None

    def build_argv(self, worker_argv: list[str], policy: SandboxPolicy) -> list[str]:
        profile = self._render_profile(policy)
        # The profile is written to a temp file; sandbox-exec will read it.
        tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".sb", prefix="spark-sbpl-", delete=False, encoding="utf-8"
        )
        tmp.write(profile)
        tmp.flush()
        tmp.close()
        return ["sandbox-exec", "-f", tmp.name, *worker_argv]

    def _render_profile(self, policy: SandboxPolicy) -> str:
        ro_rules = "\n".join(
            f'(allow file-read* (subpath "{str(Path(p).resolve())}"))'
            for p in policy.ro_paths
        )
        rw_rules = "\n".join(
            f'(allow file-read* (subpath "{str(Path(p).resolve())}"))\n'
            f'(allow file-write* (subpath "{str(Path(p).resolve())}"))'
            for p in policy.rw_paths
        )
        if policy.allow_network:
            network_rule = "(allow network-outbound)\n(allow network-bind (local ip))"
        else:
            network_rule = "(deny network-outbound)\n(deny network-bind)"
        python_prefix = str(Path(sys.prefix).resolve())
        return _PROFILE_TEMPLATE.format(
            python_prefix=python_prefix,
            ro_rules=ro_rules,
            rw_rules=rw_rules,
            network_rule=network_rule,
        )
