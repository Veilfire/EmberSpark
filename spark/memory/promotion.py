"""Memory write pipeline: classify → redact → dedup → quarantine →
contradiction-check → embed → write (M1).

All promotion paths funnel through :func:`promote`. Extensions added
in M1:

- Anti-pattern flag on the candidate propagates to the row.
- Provenance JSON recorded on the row.
- Heuristic quarantine catches obvious prompt-injection patterns
  (T4.6) — suspicious memories are written with ``status='pending_review'``
  and are invisible to retrieval until an operator approves.
- Optional contradiction check (T2.1) — if a ``chat_model`` is
  passed, contradictions get a notification + contradicts_with link.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from spark.config.enums import (
    MemoryType,
    PrivacyMode,
    RetentionClass,
    Sensitivity,
    SourceType,
)
from spark.memory.long_term import LongTermMemory, MemoryRecord
from spark.persistence.db import session_scope
from spark.persistence.models import LongTermMemoryIndexRow
from spark.persistence.repositories import LongTermMemoryIndexRepository
from spark.privacy.redaction import redact
from spark.privacy.sensitivity import decide
from spark.utils.hashing import sha256_text
from spark.utils.ids import new_memory_id


@dataclass
class MemoryCandidate:
    summary: str
    canonical_text: str
    memory_type: MemoryType = MemoryType.LESSON
    source_type: SourceType = SourceType.REFLECTION
    sensitivity: Sensitivity = Sensitivity.LOW
    retention_class: RetentionClass = RetentionClass.REVIEW
    confidence: float = 0.5
    tags: list[str] | None = None
    # M1 additions — all optional so existing callers keep working.
    is_anti_pattern: bool = False
    valid_from: Any = None  # datetime | None
    valid_until: Any = None
    provenance: dict[str, Any] | None = None
    trusted: bool = True  # untrusted => quarantine gate more aggressive


class MemoryPromoted:
    def __init__(
        self, memory_id: str, duplicate: bool, status: str = "active"
    ) -> None:
        self.memory_id = memory_id
        self.duplicate = duplicate
        self.status = status


class MemoryRejected(Exception):
    pass


async def promote(
    *,
    long_term: LongTermMemory,
    candidate: MemoryCandidate,
    agent_id: str,
    privacy_mode: PrivacyMode,
    task_id: str | None = None,
    session_id: str | None = None,
    chat_model: Any | None = None,
) -> MemoryPromoted:
    gate = decide(privacy_mode, candidate.sensitivity)
    if not gate.allow_long_term:
        raise MemoryRejected(
            f"sensitivity {candidate.sensitivity.value} blocked from long-term under {privacy_mode.value}"
        )

    # Data-class guardrail: blocks or redacts on the memory-write scope
    # before the canonical hash + embedding are computed. A block here
    # throws SparkError(DATA_CLASS_BLOCKED); promotion is refused.
    from spark.config.enums import DataScope  # noqa: PLC0415
    from spark.errors.codes import SparkError  # noqa: PLC0415
    from spark.privacy.guardrails import apply_guardrails  # noqa: PLC0415

    try:
        summary_outcome = await apply_guardrails(
            candidate.summary,
            agent_name=agent_id,
            scope=DataScope.MEMORY_WRITE,
        )
        canonical_outcome = await apply_guardrails(
            candidate.canonical_text,
            agent_name=agent_id,
            scope=DataScope.MEMORY_WRITE,
        )
    except SparkError as exc:
        raise MemoryRejected(
            f"memory write blocked by data-class guardrail: {exc.message}"
        ) from exc

    use_presidio = privacy_mode != PrivacyMode.REGEX_ONLY
    redacted_summary = redact(
        summary_outcome.text, use_presidio=use_presidio
    ).text
    redacted_canonical = redact(
        canonical_outcome.text, use_presidio=use_presidio
    ).text

    canonical_hash = sha256_text(redacted_canonical)

    # T4.6 — heuristic quarantine. Untrusted sources are always routed
    # through review.
    from spark.memory.lifecycle import should_quarantine  # noqa: PLC0415

    quarantine, q_reason = should_quarantine(
        redacted_summary, redacted_canonical
    )
    if not candidate.trusted:
        quarantine = True
        q_reason = q_reason or "untrusted source"
    status = "pending_review" if quarantine else "active"

    import json as _json  # noqa: PLC0415

    provenance_json: str | None = None
    if candidate.provenance is not None:
        try:
            provenance_json = _json.dumps(candidate.provenance, default=str)
        except (TypeError, ValueError):
            provenance_json = None

    async with session_scope() as session:
        repo = LongTermMemoryIndexRepository(session)
        existing = await repo.find_by_hash(
            long_term.namespace, canonical_hash
        )
        if existing is not None:
            return MemoryPromoted(
                memory_id=existing.memory_id,
                duplicate=True,
                status=existing.status,
            )

        memory_id = new_memory_id()
        record = MemoryRecord(
            memory_id=memory_id,
            agent_id=agent_id,
            namespace=long_term.namespace,
            content_summary=redacted_summary,
            canonical_text=redacted_canonical,
            memory_type=candidate.memory_type,
            source_type=candidate.source_type,
            sensitivity=candidate.sensitivity,
            retention_class=candidate.retention_class,
            confidence=candidate.confidence,
            tags=candidate.tags or [],
            task_id=task_id,
            session_id=session_id,
        )
        # Only write to Chroma when not quarantined — quarantined rows
        # stay SQL-only so they cannot be retrieved.
        if status == "active":
            long_term.upsert(record)

        await repo.upsert(
            LongTermMemoryIndexRow(
                memory_id=memory_id,
                agent_name=agent_id,
                namespace=long_term.namespace,
                collection=long_term.collection_name,
                memory_type=candidate.memory_type.value,
                source_type=candidate.source_type.value,
                sensitivity=candidate.sensitivity.value,
                retention_class=candidate.retention_class.value,
                confidence=candidate.confidence,
                content_summary=redacted_summary,
                canonical_hash=canonical_hash,
                tags=",".join(candidate.tags or []),
                task_id=task_id,
                session_id=session_id,
                is_anti_pattern=candidate.is_anti_pattern,
                valid_from=candidate.valid_from,
                valid_until=candidate.valid_until,
                provenance_json=provenance_json,
                status=status,
            )
        )

        # Audit quarantine + fire review notification.
        if status == "pending_review":
            from spark.persistence.learning_repos import (  # noqa: PLC0415
                AuditRepository,
            )

            await AuditRepository(session).append(
                actor=agent_id,
                kind="memory.quarantined",
                target=memory_id,
                diff={"reason": q_reason, "sensitivity": candidate.sensitivity.value},
                severity="elevated",
                reason=q_reason,
            )

    # Contradiction check runs after the write so both sides have IDs.
    if status == "active" and chat_model is not None:
        try:
            from spark.memory.lifecycle import (  # noqa: PLC0415
                detect_contradiction,
                mark_contradictions,
            )

            result = await detect_contradiction(
                new_summary=redacted_summary,
                new_memory_type=candidate.memory_type.value,
                namespace=long_term.namespace,
                long_term=long_term,
                embedder=long_term.embedder,
                chat_model=chat_model,
            )
            if result.contradicts and result.other_memory_id:
                await mark_contradictions(
                    new_memory_id=memory_id,
                    other_memory_id=result.other_memory_id,
                    reason=result.reason,
                )
                try:
                    from spark.notifications import (  # noqa: PLC0415
                        NotificationKind,
                        get_notification_service,
                    )

                    await get_notification_service().notify(
                        kind=NotificationKind.MEMORY_CONTRADICTION,
                        title="Memory contradiction detected",
                        body=result.reason[:200],
                        severity="elevated",
                        target_kind="memory",
                        target_id=memory_id,
                        action_url="/memory",
                    )
                except Exception:
                    pass
        except Exception:
            pass

    return MemoryPromoted(
        memory_id=memory_id, duplicate=False, status=status
    )
