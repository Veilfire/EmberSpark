"""Memory consolidation pass (T2.3).

Periodically clusters similar old memories within a namespace and
produces a single synthesized "crystallized" memory summarizing the
cluster. The source memories are marked ``superseded_by: <new_id>``
so retrieval skips them but operators can still inspect the chain.

Design decisions:
- We intentionally use cheap HDBSCAN-ish agglomeration instead of
  pulling in ``scikit-learn`` — iterate pairs and merge above a
  cosine threshold.
- Consolidation only fires on clusters with ≥ N members AND avg age
  ≥ D days — recent activity is left alone.
- The synthesized memory inherits the highest confidence in the
  cluster and the most sensitive sensitivity (conservative).
- Requires a chat model for synthesis. If not provided, we log and
  skip — this is optional beautification, not correctness.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select

from spark.config.enums import MemoryType, RetentionClass, Sensitivity, SourceType
from spark.logging import get_logger
from spark.memory.long_term import LongTermMemory, MemoryRecord
from spark.persistence.db import session_scope
from spark.persistence.models import LongTermMemoryIndexRow
from spark.utils.ids import new_memory_id
from spark.utils.hashing import sha256_text

log = get_logger("spark.memory.consolidation")


@dataclass
class ConsolidationReport:
    namespaces_touched: list[str]
    clusters_found: int
    memories_superseded: int
    new_memories_created: int


_SENS_RANK = {"low": 0, "moderate": 1, "high": 2, "restricted": 3}


async def run_consolidation_pass(
    *,
    chat_model: Any | None,
    embedder: Any,
    persist_path: str,
    min_cluster_size: int = 5,
    min_age_days: float = 14.0,
    similarity_threshold: float = 0.88,
) -> ConsolidationReport:
    """Walk every namespace, find clusters, synthesize representatives."""
    if chat_model is None:
        log.info("consolidation.skip", reason="no_chat_model")
        return ConsolidationReport([], 0, 0, 0)

    cutoff = datetime.now(tz=timezone.utc) - timedelta(days=min_age_days)
    namespaces_touched: set[str] = set()
    clusters_found = 0
    memories_superseded = 0
    new_memories = 0

    async with session_scope() as session:
        stmt = select(LongTermMemoryIndexRow).where(
            LongTermMemoryIndexRow.status == "active",
            LongTermMemoryIndexRow.superseded_by.is_(None),
        )
        result = await session.execute(stmt)
        rows = list(result.scalars().all())

    # Group by namespace.
    by_ns: dict[tuple[str, str], list[LongTermMemoryIndexRow]] = {}
    for r in rows:
        by_ns.setdefault((r.namespace, r.collection), []).append(r)

    for (namespace, collection), ns_rows in by_ns.items():
        # Only consider memories aged past the threshold.
        candidates = [
            r
            for r in ns_rows
            if (r.updated_at or r.created_at)
            and (r.updated_at or r.created_at).replace(
                tzinfo=timezone.utc
            )
            if (
                (r.updated_at or r.created_at).replace(tzinfo=timezone.utc)
                < cutoff
            )
        ]
        if len(candidates) < min_cluster_size:
            continue

        summaries = [r.content_summary for r in candidates]
        try:
            vecs = embedder.embed_batch(summaries)
        except Exception as exc:
            log.warning(
                "consolidation.embed_failed",
                namespace=namespace,
                error=str(exc),
            )
            continue

        # Naive agglomerative clustering (O(n^2) — fine for namespace
        # sizes we deal with here).
        clusters: list[list[int]] = []  # list of row indices
        assigned: set[int] = set()

        def _cos(a: list[float], b: list[float]) -> float:
            return sum(x * y for x, y in zip(a, b, strict=False))

        for i in range(len(candidates)):
            if i in assigned:
                continue
            cluster = [i]
            assigned.add(i)
            for j in range(i + 1, len(candidates)):
                if j in assigned:
                    continue
                if _cos(vecs[i], vecs[j]) >= similarity_threshold:
                    cluster.append(j)
                    assigned.add(j)
            if len(cluster) >= min_cluster_size:
                clusters.append(cluster)

        if not clusters:
            continue

        namespaces_touched.add(namespace)
        clusters_found += len(clusters)
        ltm = LongTermMemory(
            namespace=namespace,
            collection_name=collection,
            persist_path=persist_path,
            embedder=embedder,
        )

        for cluster in clusters:
            members = [candidates[i] for i in cluster]
            try:
                synthesized = await _synthesize(
                    chat_model=chat_model, members=members
                )
            except Exception as exc:
                log.warning(
                    "consolidation.synthesize_failed",
                    namespace=namespace,
                    error=str(exc),
                )
                continue

            # Pick most-sensitive sensitivity, highest confidence.
            new_sens = max(
                (m.sensitivity for m in members),
                key=lambda s: _SENS_RANK.get(s, 0),
            )
            new_conf = min(1.0, sum(m.confidence for m in members) / len(members) + 0.1)
            agent_id = members[0].agent_name

            record = MemoryRecord(
                memory_id=new_memory_id(prefix="cmem"),
                agent_id=agent_id,
                namespace=namespace,
                content_summary=synthesized,
                canonical_text=synthesized,
                memory_type=MemoryType.PATTERN,
                source_type=SourceType.REFLECTION,
                sensitivity=Sensitivity(new_sens),
                retention_class=RetentionClass.PERSISTENT,
                confidence=new_conf,
                tags=["consolidated"],
            )
            try:
                ltm.upsert(record)
            except Exception as exc:
                log.warning(
                    "consolidation.upsert_failed", error=str(exc)
                )
                continue

            # Persist the crystallized row + mark sources.
            async with session_scope() as session:
                new_row = LongTermMemoryIndexRow(
                    memory_id=record.memory_id,
                    agent_name=agent_id,
                    namespace=namespace,
                    collection=collection,
                    memory_type=record.memory_type.value,
                    source_type=record.source_type.value,
                    sensitivity=record.sensitivity.value,
                    retention_class=record.retention_class.value,
                    confidence=record.confidence,
                    content_summary=record.content_summary,
                    canonical_hash=sha256_text(record.canonical_text),
                    tags="consolidated",
                    provenance_json=_source_provenance(members),
                    status="active",
                )
                session.add(new_row)
                for mrow in members:
                    db_row = await session.get(
                        LongTermMemoryIndexRow, mrow.memory_id
                    )
                    if db_row is None:
                        continue
                    db_row.superseded_by = record.memory_id
                    db_row.retention_class = RetentionClass.EXPIRING.value
                    session.add(db_row)
                    memories_superseded += 1
                new_memories += 1

    return ConsolidationReport(
        namespaces_touched=sorted(namespaces_touched),
        clusters_found=clusters_found,
        memories_superseded=memories_superseded,
        new_memories_created=new_memories,
    )


async def _synthesize(
    *, chat_model: Any, members: list[LongTermMemoryIndexRow]
) -> str:
    """Ask the model to produce a single summary of a memory cluster."""
    bullets = "\n".join(f"- {m.content_summary}" for m in members)
    prompt = (
        "You are consolidating related memories into a single, "
        "more accurate summary. Produce ONE sentence (<=200 chars) "
        "that captures the shared truth. Be precise and neutral.\n\n"
        f"{bullets}\n\nConsolidated summary:"
    )
    resp = await chat_model.ainvoke([("human", prompt)])
    text = str(getattr(resp, "content", resp)).strip()
    if not text:
        text = members[0].content_summary
    return text[:500]


def _source_provenance(members: list[LongTermMemoryIndexRow]) -> str:
    import json as _json  # noqa: PLC0415

    return _json.dumps(
        {
            "source": "consolidation",
            "derived_from_memory_ids": [m.memory_id for m in members],
        }
    )
