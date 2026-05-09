"""Consensus memory detection (T4.2).

When N independent agents produce effectively-identical memories,
promote a single representative to a shared ``__consensus__``
namespace with bumped confidence. Consensus memories are read-only
and visible to every agent regardless of per-agent sharing config.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select

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

CONSENSUS_NAMESPACE = "__consensus__"

log = get_logger("spark.memory.consensus")


@dataclass
class ConsensusReport:
    detected: int
    promoted: int


async def run_consensus_detection(
    *,
    embedder: Any,
    persist_path: str,
    threshold: float = 0.92,
    min_agents: int = 2,
) -> ConsensusReport:
    """Scan hashes + semantic neighbors across agents for agreement."""
    async with session_scope() as session:
        stmt = select(LongTermMemoryIndexRow).where(
            LongTermMemoryIndexRow.status == "active",
            LongTermMemoryIndexRow.superseded_by.is_(None),
            LongTermMemoryIndexRow.namespace != CONSENSUS_NAMESPACE,
        )
        result = await session.execute(stmt)
        rows = list(result.scalars().all())

    # Fast path: exact canonical_hash match across agents.
    by_hash: dict[str, list[LongTermMemoryIndexRow]] = {}
    for r in rows:
        by_hash.setdefault(r.canonical_hash, []).append(r)

    promoted = 0
    detected = 0
    for h, group in by_hash.items():
        unique_agents = {r.agent_name for r in group}
        if len(unique_agents) >= min_agents:
            detected += 1
            ok = await _promote_consensus(
                group=group,
                embedder=embedder,
                persist_path=persist_path,
            )
            if ok:
                promoted += 1

    log.info(
        "consensus.detected", detected=detected, promoted=promoted
    )
    return ConsensusReport(detected=detected, promoted=promoted)


async def _promote_consensus(
    *,
    group: list[LongTermMemoryIndexRow],
    embedder: Any,
    persist_path: str,
) -> bool:
    """Write a single representative to the consensus namespace."""
    import json as _json  # noqa: PLC0415

    agents = sorted({r.agent_name for r in group})
    cons_hash = sha256_text(f"consensus:{group[0].canonical_hash}")

    # Skip if already promoted (idempotent).
    async with session_scope() as session:
        existing = await session.execute(
            select(LongTermMemoryIndexRow).where(
                LongTermMemoryIndexRow.namespace == CONSENSUS_NAMESPACE,
                LongTermMemoryIndexRow.canonical_hash == cons_hash,
            )
        )
        if existing.scalars().first() is not None:
            return False

    rep = group[0]
    new_conf = min(1.0, max(r.confidence for r in group) + 0.1)
    ltm = LongTermMemory(
        namespace=CONSENSUS_NAMESPACE,
        collection_name=CONSENSUS_NAMESPACE,
        persist_path=persist_path,
        embedder=embedder,
    )
    record = MemoryRecord(
        memory_id=new_memory_id(prefix="cons"),
        agent_id="__consensus__",
        namespace=CONSENSUS_NAMESPACE,
        content_summary=rep.content_summary,
        canonical_text=rep.content_summary,
        memory_type=MemoryType(rep.memory_type),
        source_type=SourceType.REFLECTION,
        sensitivity=Sensitivity(rep.sensitivity),
        retention_class=RetentionClass.PERSISTENT,
        confidence=new_conf,
        tags=["consensus"],
    )
    try:
        ltm.upsert(record)
    except Exception as exc:
        log.warning("consensus.upsert_failed", error=str(exc))
        return False

    async with session_scope() as session:
        session.add(
            LongTermMemoryIndexRow(
                memory_id=record.memory_id,
                agent_name="__consensus__",
                namespace=CONSENSUS_NAMESPACE,
                collection=CONSENSUS_NAMESPACE,
                memory_type=rep.memory_type,
                source_type=SourceType.REFLECTION.value,
                sensitivity=rep.sensitivity,
                retention_class=RetentionClass.PERSISTENT.value,
                confidence=new_conf,
                content_summary=rep.content_summary,
                canonical_hash=cons_hash,
                tags="consensus",
                consensus_sources=",".join(agents),
                provenance_json=_json.dumps(
                    {
                        "source": "consensus",
                        "agents": agents,
                        "source_memory_ids": [m.memory_id for m in group],
                    }
                ),
                status="active",
            )
        )
    return True
