"""Continuous learning subsystem.

Three layers:
- **Reflective** (A) — enhanced reflection produces success metrics + lessons.
- **Strategic** (B) — named playbooks with Thompson-sampling bandit selection.
- **Skill** (C) — see ``spark.skills`` for the agent-developed skills subsystem
  that shares this module's persistence spine.
"""

from __future__ import annotations

from spark.learning.bandit import (
    BanditScore,
    select_playbook,
    update_beta_posterior,
)
from spark.learning.fingerprint import compute_fingerprint
from spark.learning.playbooks import (
    Playbook,
    PlaybookCandidate,
    PlaybookStore,
)
from spark.learning.reflection_plus import (
    EnhancedReflectionInput,
    EnhancedReflectionResult,
)

__all__ = [
    "BanditScore",
    "EnhancedReflectionInput",
    "EnhancedReflectionResult",
    "Playbook",
    "PlaybookCandidate",
    "PlaybookStore",
    "compute_fingerprint",
    "select_playbook",
    "update_beta_posterior",
]
