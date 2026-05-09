"""Stable fingerprint for a task objective + tool sequence.

The fingerprint is a deterministic hash of a normalized tuple:
    (lowercase objective, sorted tool names)

It is *not* a fine-grained strategy hash — it is coarse enough that near-duplicate
tasks collide onto the same playbook, which is exactly what we want for bandit
reuse. Use `spark.learning.playbooks.PlaybookStore.find_applicable` for semantic
similarity matching on top of fingerprint equality.
"""

from __future__ import annotations

import re

from spark.utils.hashing import sha256_text

_WS = re.compile(r"\s+")
_NONALNUM = re.compile(r"[^a-z0-9\s]")


def _normalize_objective(objective: str) -> str:
    text = objective.lower().strip()
    text = _NONALNUM.sub(" ", text)
    text = _WS.sub(" ", text).strip()
    # Drop stop-ish words that don't change intent.
    drop = {"the", "a", "an", "and", "or", "to", "of", "for", "with", "into"}
    return " ".join(w for w in text.split() if w not in drop)


def compute_fingerprint(objective: str, tool_sequence: list[str]) -> str:
    normalized = _normalize_objective(objective)
    tools = "|".join(sorted(set(tool_sequence)))
    return sha256_text(f"{normalized}\n{tools}")[:32]
