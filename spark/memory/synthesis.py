"""Memory "dreams" — periodic synthesis of higher-order patterns (T4.1).

Creative, not mechanical. Samples recent memories and asks the model
to notice patterns, hypotheses, and violated constraints. Outputs
new low-confidence memories that must be confirmed by a real run
before they graduate.

Distinct from consolidation (T2.3):
- Consolidation *compresses* existing clusters.
- Synthesis *generates* new abstractions across dissimilar memories.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field

from spark.config.enums import (
    MemoryType,
    RetentionClass,
    Sensitivity,
    SourceType,
)
from spark.logging import get_logger
from spark.memory.long_term import LongTermMemory, MemoryRecord
from spark.persistence.db import session_scope
from spark.persistence.models import LongTermMemoryIndexRow
from spark.utils.hashing import sha256_text
from spark.utils.ids import new_memory_id

log = get_logger("spark.memory.synthesis")


class SynthesisRecord(BaseModel):
    """Structured output we ask the model for."""

    observations: list[str] = Field(default_factory=list, max_length=10)
    hypotheses: list[str] = Field(default_factory=list, max_length=5)
    constraint_candidates: list[str] = Field(
        default_factory=list, max_length=5
    )


@dataclass
class SynthesisReport:
    agent_name: str
    observations: int
    hypotheses_added: int
    constraints_added: int


async def run_synthesis_for_agent(
    *,
    chat_model: Any,
    long_term: LongTermMemory,
    agent_name: str,
    sample_size: int = 40,
) -> SynthesisReport:
    """Sample memories + ask the model to recombine."""
    from sqlalchemy import select  # noqa: PLC0415

    async with session_scope() as session:
        stmt = (
            select(LongTermMemoryIndexRow)
            .where(
                LongTermMemoryIndexRow.namespace == long_term.namespace,
                LongTermMemoryIndexRow.status == "active",
                LongTermMemoryIndexRow.superseded_by.is_(None),
            )
            .order_by(LongTermMemoryIndexRow.updated_at.desc())
            .limit(200)
        )
        result = await session.execute(stmt)
        rows = list(result.scalars().all())

    if len(rows) < 10:
        return SynthesisReport(agent_name, 0, 0, 0)

    sampled = random.sample(rows, min(sample_size, len(rows)))
    bullets = "\n".join(
        f"- ({r.memory_type}) {r.content_summary}" for r in sampled
    )

    prompt = (
        "You are reviewing an agent's recent observations. Your job is "
        "to notice higher-order patterns the agent hasn't articulated. "
        "Respond with STRICT JSON: "
        '{"observations": [], "hypotheses": [], "constraint_candidates": []}.\n\n'
        "- observations: factual patterns ('agent tends to X when Y')\n"
        "- hypotheses: testable claims ('X improves success on Y')\n"
        "- constraint_candidates: rules that seem to hold ('never X')\n\n"
        f"MEMORIES:\n{bullets}\n\nJSON:"
    )

    try:
        resp = await chat_model.ainvoke([("human", prompt)])
        text = str(getattr(resp, "content", resp))
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end < 0:
            return SynthesisReport(agent_name, 0, 0, 0)
        record = SynthesisRecord.model_validate_json(text[start : end + 1])
    except Exception as exc:
        log.warning("synthesis.failed", error=str(exc))
        return SynthesisReport(agent_name, 0, 0, 0)

    added_hyp = 0
    added_con = 0
    provenance = {
        "source": "synthesis",
        "derived_from_memory_ids": [r.memory_id for r in sampled],
    }

    async with session_scope() as session:
        for h in record.hypotheses:
            _write_synthesis_row(
                session,
                long_term=long_term,
                agent_name=agent_name,
                summary=h,
                memory_type=MemoryType.PATTERN,
                provenance=provenance,
            )
            added_hyp += 1
        for c in record.constraint_candidates:
            _write_synthesis_row(
                session,
                long_term=long_term,
                agent_name=agent_name,
                summary=c,
                memory_type=MemoryType.CONSTRAINT,
                provenance=provenance,
            )
            added_con += 1

    log.info(
        "synthesis.ran",
        agent=agent_name,
        observations=len(record.observations),
        hypotheses_added=added_hyp,
        constraints_added=added_con,
    )
    return SynthesisReport(
        agent_name=agent_name,
        observations=len(record.observations),
        hypotheses_added=added_hyp,
        constraints_added=added_con,
    )


def _write_synthesis_row(
    session: Any,
    *,
    long_term: LongTermMemory,
    agent_name: str,
    summary: str,
    memory_type: MemoryType,
    provenance: dict[str, Any],
) -> None:
    import json as _json  # noqa: PLC0415

    mid = new_memory_id(prefix="smem")
    record = MemoryRecord(
        memory_id=mid,
        agent_id=agent_name,
        namespace=long_term.namespace,
        content_summary=summary,
        canonical_text=summary,
        memory_type=memory_type,
        source_type=SourceType.REFLECTION,
        sensitivity=Sensitivity.LOW,
        retention_class=RetentionClass.REVIEW,
        confidence=0.3,  # hypothetical — must earn confidence via use
    )
    try:
        long_term.upsert(record)
    except Exception:
        return

    session.add(
        LongTermMemoryIndexRow(
            memory_id=mid,
            agent_name=agent_name,
            namespace=long_term.namespace,
            collection=long_term.collection_name,
            memory_type=memory_type.value,
            source_type=SourceType.REFLECTION.value,
            sensitivity=Sensitivity.LOW.value,
            retention_class=RetentionClass.REVIEW.value,
            confidence=0.3,
            content_summary=summary,
            canonical_hash=sha256_text(summary),
            tags="synthesis",
            provenance_json=_json.dumps(provenance),
            status="active",
        )
    )
