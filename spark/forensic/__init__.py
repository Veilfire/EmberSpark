"""Forensic review (H2).

Per-run, opt-in, encrypted-at-rest capture of the full chain of
thought for a task run — prompts, model responses, tool calls,
memory reads and writes.

**Security properties:**

- Per-run X25519 identity stored via the ``SecretManager``
  (i.e. the age vault). Delete the identity and the snapshots
  become permanently unreadable — cryptographic shred.
- 7-day default TTL enforced by a nightly retention sweep.
- Admin role is required to read, enable, wipe, or export.
- Zero overhead when ``spec.forensic.enabled: false`` — the
  default — because no writer is threaded through the engine loop.
"""

from __future__ import annotations

from spark.forensic.schemas import (
    ForensicMemorySnapshot,
    ForensicModelSnapshot,
    ForensicPromptSnapshot,
    ForensicReflectionSnapshot,
    ForensicSnapshotKind,
    ForensicToolSnapshot,
)
from spark.forensic.writer import ForensicWriter

__all__ = [
    "ForensicMemorySnapshot",
    "ForensicModelSnapshot",
    "ForensicPromptSnapshot",
    "ForensicReflectionSnapshot",
    "ForensicSnapshotKind",
    "ForensicToolSnapshot",
    "ForensicWriter",
]
