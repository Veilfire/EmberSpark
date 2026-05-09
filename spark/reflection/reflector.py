"""Reflection pass.

Runs after a task completes. Asks the model (via `with_structured_output`) for
a `ReflectionRecord`. Memory candidates it produces are routed through the
standard promotion pipeline — reflection cannot bypass redaction.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from spark.config.enums import PrivacyMode
from spark.logging import EventType, get_logger
from spark.memory.long_term import LongTermMemory
from spark.memory.promotion import MemoryCandidate, MemoryRejected, promote
from spark.reflection.schemas import ReflectionRecord

log = get_logger("spark.reflection")


@dataclass
class ReflectionOutcome:
    record: ReflectionRecord
    promoted_ids: list[str]
    rejected: list[str]


async def reflect(
    *,
    model: Any,
    objective: str,
    trace: list[dict[str, Any]],
    long_term: LongTermMemory | None,
    agent_id: str,
    privacy_mode: PrivacyMode,
    task_id: str | None = None,
    session_id: str | None = None,
) -> ReflectionOutcome:
    """Invoke the model to produce a structured ReflectionRecord and promote memories."""

    structured = model.with_structured_output(ReflectionRecord)
    system = (
        "You are a disciplined reflection engine for the Spark agent runtime. "
        "Produce a strict ReflectionRecord JSON. Do not include transcripts. "
        "Only propose memory_candidates that are distilled, reusable lessons."
    )
    user = json.dumps(
        {
            "objective": objective,
            "events": trace[-50:],  # cap for token budget
        },
        default=str,
    )
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]

    record: Any
    try:
        if hasattr(structured, "ainvoke"):
            record = await structured.ainvoke(messages)
        else:  # pragma: no cover — fallback for sync stubs
            record = structured.invoke(messages)
    except Exception as exc:
        # Don't surface raw provider tracebacks to the run-replay UI —
        # log them with full context here, then write a short, neutral
        # summary the operator can read at a glance. The provider error
        # is recoverable on the next run; the reflection itself is
        # advisory and the run already completed.
        log.warning(
            "reflection.model_call_failed",
            error=str(exc),
            error_class=type(exc).__name__,
        )
        record = ReflectionRecord(
            success=False,
            summary="Reflection unavailable for this run (provider rejected the structured-output schema or call failed). Run completed normally; see logs for details.",
        )

    if not isinstance(record, ReflectionRecord):
        try:
            record = ReflectionRecord.model_validate(record)
        except Exception as exc:
            log.warning(
                "reflection.schema_mismatch",
                error=str(exc),
                error_class=type(exc).__name__,
            )
            record = ReflectionRecord(
                success=False,
                summary="Reflection unavailable: model returned a payload that did not match the expected schema.",
            )

    promoted_ids: list[str] = []
    rejected: list[str] = []
    if long_term is not None and record.success:
        for payload in record.memory_candidates:
            candidate = MemoryCandidate(
                summary=payload.summary,
                canonical_text=payload.canonical_text,
                memory_type=payload.memory_type,
                sensitivity=payload.sensitivity,
                retention_class=payload.retention_class,
                confidence=payload.confidence,
                tags=payload.tags,
            )
            try:
                result = await promote(
                    long_term=long_term,
                    candidate=candidate,
                    agent_id=agent_id,
                    privacy_mode=privacy_mode,
                    task_id=task_id,
                    session_id=session_id,
                )
                promoted_ids.append(result.memory_id)
                log.info(
                    "memory promoted",
                    event_type=EventType.MEMORY_PROMOTED,
                    memory_id=result.memory_id,
                    duplicate=result.duplicate,
                )
            except MemoryRejected as exc:
                rejected.append(str(exc))

    log.info(
        "reflection completed",
        event_type=EventType.REFLECTION_COMPLETED,
        success=record.success,
        promoted=len(promoted_ids),
        rejected=len(rejected),
    )
    return ReflectionOutcome(record=record, promoted_ids=promoted_ids, rejected=rejected)
