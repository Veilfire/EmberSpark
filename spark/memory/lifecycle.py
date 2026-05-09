"""Memory lifecycle: citation credit, confidence decay, consolidation,
contradiction detection (M1).

These helpers run after retrieval + reflection to keep the memory
store healthy over time. They are all side-effect operations on the
SQLite index (and occasionally Chroma via LongTermMemory).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from sqlalchemy import select

from spark.logging import get_logger
from spark.persistence.db import session_scope
from spark.persistence.models import LongTermMemoryIndexRow

log = get_logger("spark.memory.lifecycle")


# ---------------------------------------------------------------------------
# T1.4 — citation credit
# ---------------------------------------------------------------------------


async def credit_successful_citations(memory_ids: Iterable[str]) -> None:
    """Bump ``successful_citation_count`` + Beta posterior (T4.3).

    Called after a run succeeds, for each memory retrieved during
    the run. Bumping Beta's ``alpha`` tracks the memory's
    track-record of helping.
    """
    ids = [m for m in memory_ids if m]
    if not ids:
        return
    async with session_scope() as session:
        result = await session.execute(
            select(LongTermMemoryIndexRow).where(
                LongTermMemoryIndexRow.memory_id.in_(ids)
            )
        )
        now = datetime.now(tz=timezone.utc)
        for row in result.scalars().all():
            row.successful_citation_count = (
                row.successful_citation_count or 0
            ) + 1
            row.last_cited_at = now
            row.alpha = (row.alpha or 1.0) + 1.0
            # Snap confidence up on proven usefulness.
            row.confidence = min(1.0, (row.confidence or 0.5) + 0.05)
            session.add(row)


async def penalize_unhelpful_citations(memory_ids: Iterable[str]) -> None:
    """Bump Beta ``beta`` when retrieved memories were in-context but
    the run failed. Lowers future rank without destroying the memory."""
    ids = [m for m in memory_ids if m]
    if not ids:
        return
    async with session_scope() as session:
        result = await session.execute(
            select(LongTermMemoryIndexRow).where(
                LongTermMemoryIndexRow.memory_id.in_(ids)
            )
        )
        for row in result.scalars().all():
            row.beta = (row.beta or 1.0) + 0.5
            session.add(row)


# ---------------------------------------------------------------------------
# T2.2 — spaced-repetition confidence decay
# ---------------------------------------------------------------------------


async def decay_confidence_pass(
    *,
    idle_days: float = 7.0,
    decay_factor: float = 0.98,
) -> dict[str, int]:
    """Decay confidence on memories not cited in the last ``idle_days``.

    Returns a report of how many memories decayed.
    """
    cutoff = datetime.now(tz=timezone.utc) - timedelta(days=idle_days)
    decayed = 0
    async with session_scope() as session:
        stmt = select(LongTermMemoryIndexRow).where(
            LongTermMemoryIndexRow.status == "active"
        )
        result = await session.execute(stmt)
        for row in result.scalars().all():
            last = row.last_cited_at or row.updated_at or row.created_at
            if last is None:
                continue
            if last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
            if last < cutoff:
                new_conf = (row.confidence or 0.5) * decay_factor
                if abs(new_conf - (row.confidence or 0.5)) > 1e-4:
                    row.confidence = new_conf
                    session.add(row)
                    decayed += 1
    log.info("memory.decay_pass", decayed=decayed)
    return {"decayed": decayed}


# ---------------------------------------------------------------------------
# T2.1 — contradiction detection (called at promote time)
# ---------------------------------------------------------------------------


@dataclass
class ContradictionResult:
    contradicts: bool
    reason: str
    other_memory_id: str | None = None


async def detect_contradiction(
    *,
    new_summary: str,
    new_memory_type: str,
    namespace: str,
    long_term: Any,
    embedder: Any,
    chat_model: Any | None = None,
    similarity_threshold: float = 0.85,
) -> ContradictionResult:
    """Check whether an incoming memory contradicts an existing one.

    Fast path: no semantic near-neighbors, no contradiction.
    Slow path: ask the chat model a small structured question.
    """
    # Only fact-shaped memories are worth checking — lessons and
    # patterns are softer.
    if new_memory_type not in ("fact", "preference", "constraint"):
        return ContradictionResult(False, "skipped (non-factual type)")

    hits = long_term.query(new_summary, top_k=3)
    candidates = []
    for h in hits:
        meta = h.get("metadata") or {}
        distance = float(h.get("distance", 1.0))
        similarity = max(0.0, 1.0 - distance)
        if similarity >= similarity_threshold:
            candidates.append(
                (
                    str(meta.get("memory_id", "")),
                    str(meta.get("content_summary", h.get("document", ""))),
                    similarity,
                )
            )
    if not candidates:
        return ContradictionResult(False, "no near neighbors")

    if chat_model is None:
        return ContradictionResult(False, "no model available")

    # Ask the model. Cheap + bounded prompt.
    joined = "\n".join(
        f"- {cid}: {csum}" for cid, csum, _ in candidates
    )
    prompt = (
        "Does the NEW observation contradict any EXISTING memories? "
        "Respond in strict JSON: "
        '{"contradicts": true|false, "which": "<memory_id or empty>", '
        '"reason": "<short>"}.\n\n'
        f"NEW: {new_summary}\n\nEXISTING:\n{joined}\n"
    )
    try:
        resp = await chat_model.ainvoke([("human", prompt)])
        text = str(getattr(resp, "content", resp))
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end < 0:
            return ContradictionResult(False, "no JSON in response")
        data = json.loads(text[start : end + 1])
        return ContradictionResult(
            contradicts=bool(data.get("contradicts", False)),
            reason=str(data.get("reason", ""))[:500],
            other_memory_id=(data.get("which") or None),
        )
    except Exception as exc:
        log.warning("contradiction.check_failed", error=str(exc))
        return ContradictionResult(False, f"error: {exc}")


async def mark_contradictions(
    new_memory_id: str,
    other_memory_id: str,
    reason: str,
) -> None:
    """Record a contradiction link on both sides."""
    async with session_scope() as session:
        a = await session.get(LongTermMemoryIndexRow, new_memory_id)
        b = await session.get(LongTermMemoryIndexRow, other_memory_id)
        if a is None or b is None:
            return
        a.contradicts_with = _append_csv(a.contradicts_with, other_memory_id)
        b.contradicts_with = _append_csv(b.contradicts_with, new_memory_id)
        session.add(a)
        session.add(b)
        from spark.persistence.learning_repos import AuditRepository  # noqa: PLC0415

        await AuditRepository(session).append(
            actor="memory.lifecycle",
            kind="memory.contradiction",
            target=new_memory_id,
            diff={"other": other_memory_id, "reason": reason},
            severity="info",
        )


def _append_csv(existing: str | None, value: str) -> str:
    parts = set((existing or "").split(","))
    parts.discard("")
    parts.add(value)
    return ",".join(sorted(parts))


# ---------------------------------------------------------------------------
# T4.6 — adversarial injection quarantine
# ---------------------------------------------------------------------------


# Heuristic patterns flagging suspicious candidates. Not exhaustive —
# defense in depth via operator review queue (T5.5).
_QUARANTINE_PATTERNS = [
    # Elevated-side-effect commands with imperative framing
    (r"\b(rm\s+-rf|mkfs|dd\s+if=|wipefs|shred)\b", "destructive shell cmd"),
    (r"\bchmod\s+-R\s+0?777\b", "mass chmod"),
    (r"\b(curl|wget)\s+[^|]+\|\s*(sh|bash)\b", "pipe to shell"),
    (
        r"\b(ignore|disregard|forget)\b.*\b(previous|prior|above)\b.*\b(instructions?|prompt)\b",
        "prompt-injection phrasing",
    ),
    (r"\bbase64\s+-d\s*\|\s*(sh|bash)\b", "obfuscated exec"),
    (
        r"(AKIA|ghp_|xoxb-|sk-ant-|sk-or-v1-)\w{16,}",
        "credential-shaped token",
    ),
]


def should_quarantine(summary: str, canonical_text: str) -> tuple[bool, str]:
    """Return (quarantine?, reason). Runs against summary + canonical."""
    import re as _re  # noqa: PLC0415

    text = f"{summary or ''}\n{canonical_text or ''}"
    for pattern, reason in _QUARANTINE_PATTERNS:
        if _re.search(pattern, text, _re.IGNORECASE | _re.DOTALL):
            return True, reason
    return False, ""
