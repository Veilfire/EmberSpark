"""Memory retrieval pipeline — hybrid search, rerank, dedup, rank (M1).

Pipeline (in order):
1. Candidate gather:
   a. Semantic (Chroma) top-K*3
   b. BM25 (sidecar) top-K*3  — if ``rank_bm25`` installed
2. Fuse via Reciprocal Rank Fusion (RRF, k=60)
3. SQL enrichment + status / temporal / superseded gating
4. Sensitivity filter via ``decide(privacy_mode, sensitivity)``
5. Optional cross-encoder rerank (sentence-transformers CrossEncoder)
6. Semantic near-duplicate collapse (cosine > 0.92)
7. Final rank with adaptive weights (recency / confidence / citation)

Anti-pattern memories are returned with ``is_anti_pattern=True`` so
callers can frame them as "avoid" in the prompt.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from spark.config.enums import PrivacyMode, Sensitivity
from spark.memory import bm25 as bm25_mod
from spark.memory.long_term import LongTermMemory
from spark.privacy.sensitivity import decide

RRF_K = 60.0


@dataclass
class RetrievedMemory:
    memory_id: str
    summary: str
    memory_type: str
    source_type: str
    sensitivity: str
    confidence: float
    score: float
    is_anti_pattern: bool = False
    duplicates: list[str] = field(default_factory=list)
    namespace: str = ""


def _recency_weight(iso_ts: str, *, half_life_days: float = 30.0) -> float:
    try:
        dt = datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return 0.5
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    age_days = max(
        0.0, (datetime.now(tz=timezone.utc) - dt).total_seconds() / 86400.0
    )
    return 0.5 ** (age_days / half_life_days)


# T1.3 — adaptive weights based on query shape.
_RECENCY_CUES = re.compile(
    r"\b(recent|latest|today|yesterday|last|just|now|currently|this week|this month)\b",
    re.IGNORECASE,
)
_FACT_CUES = re.compile(
    r"\b(what|who|when|where|why|how|is|was|are|were|does|did)\b",
    re.IGNORECASE,
)


def classify_query(query: str) -> tuple[float, float]:
    """Return (recency_weight, confidence_weight) tuned to the query.

    Cheap deterministic heuristic. Recency-flavored queries boost
    recency; factual "what is X" queries boost confidence.
    """
    q = query or ""
    if _RECENCY_CUES.search(q):
        return 0.25, 0.05
    if _FACT_CUES.search(q):
        return 0.05, 0.25
    return 0.1, 0.1


def _reciprocal_rank(ranked: list[str], k: float = RRF_K) -> dict[str, float]:
    return {mid: 1.0 / (k + i + 1) for i, mid in enumerate(ranked)}


async def _load_sql_meta(memory_ids: list[str]) -> dict[str, Any]:
    """Fetch enriched metadata for a batch of memory_ids."""
    if not memory_ids:
        return {}
    from sqlalchemy import select as _select  # noqa: PLC0415

    from spark.persistence.db import session_scope  # noqa: PLC0415
    from spark.persistence.models import LongTermMemoryIndexRow  # noqa: PLC0415

    async with session_scope() as session:
        stmt = _select(LongTermMemoryIndexRow).where(
            LongTermMemoryIndexRow.memory_id.in_(memory_ids)
        )
        result = await session.execute(stmt)
        rows = list(result.scalars().all())
    return {r.memory_id: r for r in rows}


def _temporal_valid(row: Any, now: datetime) -> bool:
    vf = getattr(row, "valid_from", None)
    vu = getattr(row, "valid_until", None)
    if vf is not None and vf > now:
        return False
    if vu is not None and vu < now:
        return False
    return True


async def retrieve(
    *,
    long_term: LongTermMemory,
    query: str,
    privacy_mode: PrivacyMode,
    top_k: int = 6,
    min_score: float = 0.72,
    recency_weight: float | None = None,
    confidence_weight: float | None = None,
    allow_types: list[str] | None = None,
    enable_rerank: bool = True,
    dedup_threshold: float = 0.92,
    exclude_memory_ids: list[str] | None = None,
    pin_memory_ids: list[str] | None = None,
) -> list[RetrievedMemory]:
    """Hybrid retrieval with reranking, dedup, and citation-aware scoring."""
    if recency_weight is None or confidence_weight is None:
        rw, cw = classify_query(query)
        recency_weight = recency_weight if recency_weight is not None else rw
        confidence_weight = (
            confidence_weight if confidence_weight is not None else cw
        )

    sem_hits = long_term.query(query, top_k=top_k * 3, where=None)
    sem_ranked: list[str] = []
    sem_by_id: dict[str, dict[str, Any]] = {}
    for h in sem_hits:
        meta = h.get("metadata") or {}
        mid = str(meta.get("memory_id", h.get("memory_id", ""))) or h.get(
            "id", ""
        )
        if not mid:
            continue
        sem_ranked.append(mid)
        sem_by_id[mid] = h

    bm25_hits = await bm25_mod.search(
        long_term.namespace, query, top_k=top_k * 3
    )
    bm25_ranked = [m for m, _ in bm25_hits]

    sem_rr = _reciprocal_rank(sem_ranked)
    bm25_rr = _reciprocal_rank(bm25_ranked)
    all_ids = set(sem_ranked) | set(bm25_ranked)
    fused = {
        mid: sem_rr.get(mid, 0.0) + bm25_rr.get(mid, 0.0) for mid in all_ids
    }

    if exclude_memory_ids:
        for x in exclude_memory_ids:
            fused.pop(x, None)
    if pin_memory_ids:
        for p in pin_memory_ids:
            fused[p] = fused.get(p, 0.0) + 2.0

    candidate_ids = list(fused.keys())
    sql_rows = await _load_sql_meta(candidate_ids)
    now = datetime.now(tz=timezone.utc)

    scored: list[RetrievedMemory] = []
    for mid in candidate_ids:
        row = sql_rows.get(mid)
        if row is None:
            continue
        status = getattr(row, "status", "active")
        if status != "active":
            continue
        if getattr(row, "superseded_by", None):
            continue
        if not _temporal_valid(row, now):
            continue
        sensitivity = Sensitivity(row.sensitivity)
        gate = decide(privacy_mode, sensitivity)
        if not gate.allow_model:
            continue
        if allow_types and row.memory_type not in allow_types:
            continue

        sem = sem_by_id.get(mid)
        if sem is not None:
            distance = float(sem.get("distance", 1.0))
            similarity = max(0.0, 1.0 - distance)
        else:
            similarity = 0.5
        if (
            sem is not None
            and similarity < min_score
            and mid not in (pin_memory_ids or [])
        ):
            continue

        confidence = float(row.confidence)
        recency = _recency_weight(str(row.created_at))
        cite_count = getattr(row, "successful_citation_count", 0) or 0
        citation_boost = 0.05 * _log1p(cite_count)

        score = similarity * max(
            0.0, 1 - recency_weight - confidence_weight
        )
        score += confidence * confidence_weight
        score += recency * recency_weight
        score += citation_boost
        score += fused.get(mid, 0.0) * 0.1

        scored.append(
            RetrievedMemory(
                memory_id=mid,
                summary=row.content_summary,
                memory_type=row.memory_type,
                source_type=row.source_type,
                sensitivity=row.sensitivity,
                confidence=confidence,
                score=score,
                is_anti_pattern=bool(getattr(row, "is_anti_pattern", False)),
                namespace=row.namespace,
            )
        )

    if enable_rerank and len(scored) > 1:
        try:
            scored = _cross_encoder_rerank(query, scored)
        except Exception:
            pass

    scored.sort(key=lambda m: m.score, reverse=True)
    if dedup_threshold < 1.0 and len(scored) > 1:
        scored = _collapse_clusters(
            scored, long_term, threshold=dedup_threshold
        )

    return scored[:top_k]


def retrieve_sync(
    *,
    long_term: LongTermMemory,
    query: str,
    privacy_mode: PrivacyMode,
    top_k: int = 6,
    min_score: float = 0.72,
    recency_weight: float = 0.1,
    confidence_weight: float = 0.1,
    allow_types: list[str] | None = None,
) -> list[RetrievedMemory]:
    """Legacy synchronous retrieval — semantic only, no SQL enrichment.

    Used by sync callers (reflection, engine preflight) that can't
    easily await. New code should call :func:`retrieve` (async).
    """
    where: dict[str, Any] = {}
    hits = long_term.query(query, top_k=top_k * 3, where=where or None)
    scored: list[RetrievedMemory] = []
    for h in hits:
        meta = h.get("metadata") or {}
        sensitivity = Sensitivity(meta.get("sensitivity", "low"))
        gate = decide(privacy_mode, sensitivity)
        if not gate.allow_model:
            continue
        if allow_types and meta.get("memory_type") not in allow_types:
            continue
        distance = float(h.get("distance", 1.0))
        similarity = max(0.0, 1.0 - distance)
        if similarity < min_score:
            continue
        confidence = float(meta.get("confidence", 0.5))
        recency = _recency_weight(str(meta.get("created_at", "")))
        score = similarity * (1 - recency_weight - confidence_weight)
        score += confidence * confidence_weight
        score += recency * recency_weight
        scored.append(
            RetrievedMemory(
                memory_id=str(meta.get("memory_id", h.get("memory_id", ""))),
                summary=str(meta.get("content_summary", h.get("document", ""))),
                memory_type=str(meta.get("memory_type", "fact")),
                source_type=str(meta.get("source_type", "reflection")),
                sensitivity=sensitivity.value,
                confidence=confidence,
                score=score,
            )
        )
    scored.sort(key=lambda m: m.score, reverse=True)
    return scored[:top_k]


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


_cross_encoder_instance: Any = None


def _get_cross_encoder() -> Any:
    global _cross_encoder_instance
    if _cross_encoder_instance is not None:
        return _cross_encoder_instance
    from sentence_transformers import CrossEncoder  # noqa: PLC0415

    _cross_encoder_instance = CrossEncoder(
        "cross-encoder/ms-marco-MiniLM-L-6-v2"
    )
    return _cross_encoder_instance


def _cross_encoder_rerank(
    query: str, memories: list[RetrievedMemory]
) -> list[RetrievedMemory]:
    N = min(20, len(memories))
    if N < 2:
        return memories
    head = memories[:N]
    tail = memories[N:]

    encoder = _get_cross_encoder()
    pairs = [(query, m.summary) for m in head]
    scores = encoder.predict(pairs)
    lo = float(min(scores))
    hi = float(max(scores))
    span = hi - lo or 1.0
    for i, m in enumerate(head):
        norm = (float(scores[i]) - lo) / span
        m.score = 0.5 * m.score + 0.5 * norm
    head.sort(key=lambda m: m.score, reverse=True)
    return head + tail


def _collapse_clusters(
    memories: list[RetrievedMemory],
    long_term: LongTermMemory,
    *,
    threshold: float = 0.92,
) -> list[RetrievedMemory]:
    if len(memories) <= 1:
        return memories
    embedder = getattr(long_term, "embedder", None)
    if embedder is None:
        return memories
    try:
        vecs = embedder.embed_batch([m.summary for m in memories])
    except Exception:
        return memories

    kept: list[RetrievedMemory] = []
    kept_vecs: list[list[float]] = []

    def _cos(a: list[float], b: list[float]) -> float:
        return sum(x * y for x, y in zip(a, b, strict=False))

    for m, v in zip(memories, vecs, strict=False):
        clustered = False
        for i, kv in enumerate(kept_vecs):
            if _cos(v, kv) >= threshold:
                kept[i].duplicates.append(m.memory_id)
                clustered = True
                break
        if not clustered:
            kept.append(m)
            kept_vecs.append(v)
    return kept


def _log1p(x: float) -> float:
    import math as _math

    return _math.log1p(x)
