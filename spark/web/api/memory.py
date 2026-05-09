"""Memory browser routes."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import or_, select

from spark.config.enums import PrivacyMode
from spark.config.runtime_config import load_runtime
from spark.memory.embeddings import SentenceTransformersProvider
from spark.memory.long_term import LongTermMemory
from spark.memory.pruning_runner import run_memory_pruning_job
from spark.memory.retrieval import retrieve
from spark.persistence.db import session_scope
from spark.persistence.learning_models import PlaybookRow
from spark.persistence.models import (
    CircleMembershipRow,
    EntityMemoryRow,
    LongTermMemoryIndexRow,
    MemoryCircleRow,
)
from spark.web.auth import Principal, require_admin, require_operator, require_viewer

router = APIRouter()


GLOBAL_NAMESPACE = "__global__"
CONSENSUS_NAMESPACE = "__consensus__"


# ---------------------------------------------------------------------------
# Manual CRUD (T5.1)
# ---------------------------------------------------------------------------


class CreateMemoryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    agent_name: str = Field(min_length=1, max_length=128)
    summary: str = Field(min_length=1, max_length=8192)
    canonical_text: str | None = Field(default=None, max_length=32_000)
    memory_type: str = "fact"
    sensitivity: str = "low"
    retention_class: str = "review"
    tags: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.7, ge=0.0, le=1.0)
    is_anti_pattern: bool = False


class UpdateMemoryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    summary: str | None = None
    sensitivity: str | None = None
    retention_class: str | None = None
    memory_type: str | None = None
    tags: list[str] | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    is_anti_pattern: bool | None = None
    status: str | None = None  # active | pending_review — operator approve flow
    valid_from: str | None = None
    valid_until: str | None = None


@router.post("/long-term")
async def create_memory(
    body: CreateMemoryRequest,
    principal: Principal = Depends(require_operator),
) -> dict[str, str]:
    """Operator-authored memory. Bypasses reflection — still redacted."""
    from pathlib import Path as _Path  # noqa: PLC0415

    from spark.config.enums import (  # noqa: PLC0415
        MemoryType,
        PrivacyMode,
        RetentionClass,
        Sensitivity,
        SourceType,
    )
    from spark.config.loader import load_agent  # noqa: PLC0415
    from spark.memory.embeddings import SentenceTransformersProvider  # noqa: PLC0415
    from spark.memory.long_term import LongTermMemory  # noqa: PLC0415
    from spark.memory.promotion import (  # noqa: PLC0415
        MemoryCandidate,
        promote,
    )
    from spark.persistence.learning_repos import AuditRepository  # noqa: PLC0415

    agent_path = _Path(f"~/.spark/agents/{body.agent_name}.yaml").expanduser()
    if not agent_path.exists():
        raise HTTPException(status_code=404, detail="agent not found")
    agent = load_agent(agent_path)
    ltm_cfg = agent.spec.memory.long_term_memory
    if ltm_cfg is None:
        raise HTTPException(
            status_code=400, detail="agent has no long_term_memory config"
        )

    ltm = LongTermMemory(
        namespace=ltm_cfg.namespace,
        collection_name=ltm_cfg.collection,
        persist_path=_resolve_chroma_path(agent),
        embedder=SentenceTransformersProvider(ltm_cfg.embedder.model),
    )
    candidate = MemoryCandidate(
        summary=body.summary,
        canonical_text=body.canonical_text or body.summary,
        memory_type=MemoryType(body.memory_type),
        source_type=SourceType.MANUAL_NOTE,
        sensitivity=Sensitivity(body.sensitivity),
        retention_class=RetentionClass(body.retention_class),
        confidence=body.confidence,
        tags=body.tags,
        is_anti_pattern=body.is_anti_pattern,
        provenance={"authored_by": principal.subject, "source": "manual"},
    )
    try:
        result = await promote(
            long_term=ltm,
            candidate=candidate,
            agent_id=body.agent_name,
            privacy_mode=PrivacyMode(agent.spec.runtime.privacy_mode),
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    async with session_scope() as session:
        await AuditRepository(session).append(
            actor=principal.subject,
            kind="memory.create_manual",
            target=result.memory_id,
            diff={"agent": body.agent_name, "summary": body.summary[:100]},
            severity="info",
        )

    return {"ok": "1", "memory_id": result.memory_id}


@router.put("/long-term/{memory_id}")
async def update_memory(
    memory_id: str,
    body: UpdateMemoryRequest,
    principal: Principal = Depends(require_operator),
) -> dict[str, bool]:
    """Edit a memory's metadata fields. Canonical text + vector not mutated."""
    from datetime import datetime as _dt  # noqa: PLC0415

    from spark.persistence.learning_repos import AuditRepository  # noqa: PLC0415

    changes = body.model_dump(exclude_unset=True)
    async with session_scope() as session:
        row = await session.get(LongTermMemoryIndexRow, memory_id)
        if row is None:
            raise HTTPException(status_code=404, detail="memory not found")

        for k, v in changes.items():
            if k == "tags" and isinstance(v, list):
                row.tags = ",".join(v)
            elif k in ("valid_from", "valid_until"):
                try:
                    setattr(
                        row, k, _dt.fromisoformat(v) if v else None
                    )
                except ValueError:
                    continue
            else:
                setattr(row, k, v)
        session.add(row)

        await AuditRepository(session).append(
            actor=principal.subject,
            kind="memory.update",
            target=memory_id,
            diff=changes,
            severity="info",
        )
    return {"ok": True}


@router.get("/long-term")
async def list_long_term(
    namespace: str | None = None,
    scope: str = "all",  # all | private | global
    limit: int = 100,
    _: Principal = Depends(require_viewer),
) -> list[dict[str, object]]:
    async with session_scope() as session:
        stmt = select(LongTermMemoryIndexRow).order_by(
            LongTermMemoryIndexRow.updated_at.desc()
        )
        if namespace is not None:
            stmt = stmt.where(LongTermMemoryIndexRow.namespace == namespace)
        elif scope == "global":
            stmt = stmt.where(LongTermMemoryIndexRow.namespace == GLOBAL_NAMESPACE)
        elif scope == "private":
            stmt = stmt.where(LongTermMemoryIndexRow.namespace != GLOBAL_NAMESPACE)
        stmt = stmt.limit(limit)
        result = await session.execute(stmt)
        rows = list(result.scalars().all())
    return [
        {
            "memory_id": r.memory_id,
            "agent_name": r.agent_name,
            "namespace": r.namespace,
            "is_global": r.namespace == GLOBAL_NAMESPACE,
            "memory_type": r.memory_type,
            "sensitivity": r.sensitivity,
            "retention_class": r.retention_class,
            "confidence": r.confidence,
            "content_summary": r.content_summary,
            "updated_at": r.updated_at,
        }
        for r in rows
    ]


_SENSITIVITY_ORDER = {"low": 0, "moderate": 1, "high": 2, "restricted": 3}


@router.post("/long-term/{memory_id}/promote-to-global")
async def promote_to_global(
    memory_id: str,
    principal: Principal = Depends(require_operator),
) -> dict[str, object]:
    """Copy a memory from its private namespace to the shared ``__global__`` pool.

    Refuses memories whose sensitivity exceeds the source agent's
    ``max_cross_scope_sensitivity``. Audited at ``elevated`` severity.
    """
    from pathlib import Path as _Path  # noqa: PLC0415

    from spark.config.loader import load_agent  # noqa: PLC0415
    from spark.memory.embeddings import SentenceTransformersProvider  # noqa: PLC0415
    from spark.memory.long_term import LongTermMemory, MemoryRecord  # noqa: PLC0415
    from spark.persistence.learning_repos import AuditRepository  # noqa: PLC0415

    async with session_scope() as session:
        row = await session.get(LongTermMemoryIndexRow, memory_id)
        if row is None:
            raise HTTPException(status_code=404, detail="memory not found")
        if row.namespace == GLOBAL_NAMESPACE:
            raise HTTPException(status_code=409, detail="already global")

        # Load source agent to check write_global + max sensitivity.
        agent_name = row.agent_name
        agent_path = _Path(f"~/.spark/agents/{agent_name}.yaml").expanduser()
        if not agent_path.exists():
            raise HTTPException(
                status_code=404,
                detail=f"source agent {agent_name!r} YAML not found",
            )
        agent = load_agent(agent_path)
        sharing = getattr(agent.spec.memory, "sharing", None)
        if sharing is None or not sharing.write_global:
            raise HTTPException(
                status_code=403,
                detail=f"agent {agent_name!r} does not allow writing to global",
            )
        cap = _SENSITIVITY_ORDER.get(sharing.max_cross_scope_sensitivity, 1)
        mem_sens = _SENSITIVITY_ORDER.get(row.sensitivity, 0)
        if mem_sens > cap:
            raise HTTPException(
                status_code=403,
                detail=(
                    f"memory sensitivity {row.sensitivity} exceeds agent's "
                    f"max_cross_scope_sensitivity {sharing.max_cross_scope_sensitivity}"
                ),
            )

        # Fetch the underlying Chroma document from the source collection.
        try:
            src_ltm = LongTermMemory(
                namespace=row.namespace,
                collection_name=row.collection,
                persist_path=_resolve_chroma_path(agent),
                embedder=SentenceTransformersProvider(),
            )
            collection = src_ltm._get_collection()  # noqa: SLF001
            got = collection.get(ids=[memory_id])
            documents = got.get("documents", [])
            metadatas = got.get("metadatas", [])
            if not documents:
                raise HTTPException(
                    status_code=404, detail="source document not in Chroma"
                )
            canonical_text = documents[0]
            meta = metadatas[0] if metadatas else {}
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(
                status_code=500, detail=f"failed to read source: {exc}"
            ) from exc

        # Write a copy into the global collection with a new id.
        import hashlib as _hl  # noqa: PLC0415

        new_id = "gmem-" + _hl.sha256(
            (memory_id + "-to-global").encode("utf-8")
        ).hexdigest()[:32]
        try:
            from spark.config.enums import (  # noqa: PLC0415
                MemoryType,
                RetentionClass,
                Sensitivity,
                SourceType,
            )

            record = MemoryRecord(
                memory_id=new_id,
                agent_id=agent_name,
                namespace=GLOBAL_NAMESPACE,
                content_summary=row.content_summary,
                canonical_text=canonical_text,
                memory_type=MemoryType(row.memory_type),
                source_type=SourceType(row.source_type),
                sensitivity=Sensitivity(row.sensitivity),
                retention_class=RetentionClass(row.retention_class),
                confidence=row.confidence,
                tags=(row.tags or "").split(","),
            )
            dst_ltm = LongTermMemory(
                namespace=GLOBAL_NAMESPACE,
                collection_name=GLOBAL_NAMESPACE,
                persist_path=_resolve_chroma_path(agent),
                embedder=SentenceTransformersProvider(),
            )
            dst_ltm.upsert(record)
        except Exception as exc:
            raise HTTPException(
                status_code=500, detail=f"failed to write to global: {exc}"
            ) from exc

        # Index row for the global copy.
        global_row = LongTermMemoryIndexRow(
            memory_id=new_id,
            agent_name=agent_name,
            namespace=GLOBAL_NAMESPACE,
            collection=GLOBAL_NAMESPACE,
            memory_type=row.memory_type,
            source_type=row.source_type,
            sensitivity=row.sensitivity,
            retention_class=row.retention_class,
            confidence=row.confidence,
            canonical_hash=row.canonical_hash,
            content_summary=row.content_summary,
            tags=row.tags,
            task_id=row.task_id,
            session_id=row.session_id,
        )
        session.add(global_row)

        await AuditRepository(session).append(
            actor=principal.subject,
            kind="memory.promote_global",
            target=memory_id,
            diff={
                "source_agent": agent_name,
                "source_namespace": row.namespace,
                "new_id": new_id,
                "sensitivity": row.sensitivity,
            },
            severity="elevated",
        )

    return {"ok": True, "new_memory_id": new_id}


# ---------------------------------------------------------------------------
# Review queue (T5.5)
# ---------------------------------------------------------------------------


@router.get("/review-queue")
async def review_queue(
    limit: int = 200,
    _: Principal = Depends(require_viewer),
) -> list[dict[str, object]]:
    """Memories that need operator attention: quarantined, contradictions,
    low confidence, or superseded."""
    async with session_scope() as session:
        stmt = (
            select(LongTermMemoryIndexRow)
            .where(
                or_(
                    LongTermMemoryIndexRow.status == "pending_review",
                    LongTermMemoryIndexRow.status == "quarantined",
                    LongTermMemoryIndexRow.contradicts_with.is_not(None),
                    LongTermMemoryIndexRow.confidence < 0.3,
                )
            )
            .order_by(LongTermMemoryIndexRow.updated_at.desc())
            .limit(limit)
        )
        result = await session.execute(stmt)
        rows = list(result.scalars().all())
    return [
        {
            "memory_id": r.memory_id,
            "agent_name": r.agent_name,
            "namespace": r.namespace,
            "status": r.status,
            "confidence": r.confidence,
            "contradicts_with": r.contradicts_with,
            "superseded_by": r.superseded_by,
            "content_summary": r.content_summary,
            "memory_type": r.memory_type,
            "sensitivity": r.sensitivity,
            "updated_at": r.updated_at,
            "reason": _review_reason(r),
        }
        for r in rows
    ]


def _review_reason(r: LongTermMemoryIndexRow) -> str:
    if r.status == "pending_review":
        return "quarantined pending review"
    if r.status == "quarantined":
        return "quarantined"
    if r.contradicts_with:
        return f"contradicts {r.contradicts_with}"
    if (r.confidence or 0) < 0.3:
        return "low confidence"
    return ""


@router.post("/long-term/{memory_id}/approve")
async def approve_memory(
    memory_id: str,
    principal: Principal = Depends(require_admin),
) -> dict[str, bool]:
    """Graduate a ``pending_review`` memory to ``active``.

    Also writes the deferred Chroma upsert so the memory becomes
    retrievable.
    """
    from pathlib import Path as _Path  # noqa: PLC0415

    from spark.config.enums import (  # noqa: PLC0415
        MemoryType,
        RetentionClass,
        Sensitivity,
        SourceType,
    )
    from spark.config.loader import load_agent  # noqa: PLC0415
    from spark.memory.long_term import MemoryRecord  # noqa: PLC0415
    from spark.persistence.learning_repos import AuditRepository  # noqa: PLC0415

    async with session_scope() as session:
        row = await session.get(LongTermMemoryIndexRow, memory_id)
        if row is None:
            raise HTTPException(status_code=404, detail="memory not found")
        if row.status == "active":
            return {"ok": True}

        # Load owning agent to get Chroma config + embedder.
        agent_path = _Path(
            f"~/.spark/agents/{row.agent_name}.yaml"
        ).expanduser()
        if agent_path.exists():
            agent = load_agent(agent_path)
            ltm = LongTermMemory(
                namespace=row.namespace,
                collection_name=row.collection,
                persist_path=_resolve_chroma_path(agent),
                embedder=SentenceTransformersProvider(
                    agent.spec.memory.long_term_memory.embedder.model
                ),
            )
            try:
                ltm.upsert(
                    MemoryRecord(
                        memory_id=row.memory_id,
                        agent_id=row.agent_name,
                        namespace=row.namespace,
                        content_summary=row.content_summary,
                        canonical_text=row.content_summary,
                        memory_type=MemoryType(row.memory_type),
                        source_type=SourceType(row.source_type),
                        sensitivity=Sensitivity(row.sensitivity),
                        retention_class=RetentionClass(
                            row.retention_class
                        ),
                        confidence=row.confidence,
                        tags=(row.tags or "").split(",") if row.tags else [],
                    )
                )
            except Exception:
                pass

        row.status = "active"
        session.add(row)
        await AuditRepository(session).append(
            actor=principal.subject,
            kind="memory.approve",
            target=memory_id,
            severity="elevated",
        )
    return {"ok": True}


@router.post("/long-term/{memory_id}/quarantine")
async def quarantine_memory(
    memory_id: str,
    principal: Principal = Depends(require_admin),
) -> dict[str, bool]:
    """Move a memory into quarantine (invisible to retrieval)."""
    from spark.persistence.learning_repos import AuditRepository  # noqa: PLC0415

    async with session_scope() as session:
        row = await session.get(LongTermMemoryIndexRow, memory_id)
        if row is None:
            raise HTTPException(status_code=404, detail="memory not found")
        row.status = "quarantined"
        session.add(row)
        await AuditRepository(session).append(
            actor=principal.subject,
            kind="memory.quarantine",
            target=memory_id,
            severity="elevated",
        )
    return {"ok": True}


# ---------------------------------------------------------------------------
# Entity memory (T3.1)
# ---------------------------------------------------------------------------


@router.get("/entities")
async def list_entities(
    namespace: str | None = None,
    subject: str | None = None,
    limit: int = 200,
    _: Principal = Depends(require_viewer),
) -> list[dict[str, object]]:
    async with session_scope() as session:
        stmt = select(EntityMemoryRow).order_by(
            EntityMemoryRow.updated_at.desc()
        )
        if namespace:
            stmt = stmt.where(EntityMemoryRow.namespace == namespace)
        if subject:
            stmt = stmt.where(EntityMemoryRow.subject == subject)
        stmt = stmt.limit(limit)
        rows = (await session.execute(stmt)).scalars().all()
    return [
        {
            "id": r.id,
            "namespace": r.namespace,
            "subject": r.subject,
            "predicate": r.predicate,
            "object": r.object,
            "confidence": r.confidence,
            "source_memory_id": r.source_memory_id,
            "valid_from": r.valid_from,
            "valid_until": r.valid_until,
            "updated_at": r.updated_at,
        }
        for r in rows
    ]


class EntityUpsertRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    namespace: str
    subject: str
    predicate: str
    object: str
    confidence: float = Field(default=0.6, ge=0.0, le=1.0)
    source_memory_id: str | None = None


@router.post("/entities")
async def upsert_entity(
    body: EntityUpsertRequest,
    principal: Principal = Depends(require_operator),
) -> dict[str, object]:
    async with session_scope() as session:
        stmt = select(EntityMemoryRow).where(
            EntityMemoryRow.namespace == body.namespace,
            EntityMemoryRow.subject == body.subject,
            EntityMemoryRow.predicate == body.predicate,
            EntityMemoryRow.object == body.object,
        )
        existing = (await session.execute(stmt)).scalars().first()
        if existing is not None:
            existing.confidence = body.confidence
            session.add(existing)
            return {"ok": True, "id": existing.id, "existed": True}
        row = EntityMemoryRow(
            namespace=body.namespace,
            subject=body.subject,
            predicate=body.predicate,
            object=body.object,
            confidence=body.confidence,
            source_memory_id=body.source_memory_id,
        )
        session.add(row)
        await session.flush()
        return {"ok": True, "id": row.id, "existed": False}


# ---------------------------------------------------------------------------
# Memory circles (T3.3)
# ---------------------------------------------------------------------------


class CircleUpsertRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    circle_id: str = Field(
        min_length=1, max_length=64, pattern=r"^[a-z0-9][a-z0-9-]{0,62}[a-z0-9]$"
    )
    name: str = Field(min_length=1, max_length=128)
    description: str = ""


class CircleMembershipRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    agent_name: str
    can_read: bool = True
    can_write: bool = False


@router.get("/circles")
async def list_circles(
    _: Principal = Depends(require_viewer),
) -> list[dict[str, object]]:
    async with session_scope() as session:
        rows = (
            await session.execute(select(MemoryCircleRow))
        ).scalars().all()
        members_res = await session.execute(select(CircleMembershipRow))
        members = list(members_res.scalars().all())
    by_circle: dict[str, list[CircleMembershipRow]] = {}
    for m in members:
        by_circle.setdefault(m.circle_id, []).append(m)
    return [
        {
            "circle_id": c.circle_id,
            "name": c.name,
            "description": c.description,
            "members": [
                {
                    "agent_name": m.agent_name,
                    "can_read": m.can_read,
                    "can_write": m.can_write,
                }
                for m in by_circle.get(c.circle_id, [])
            ],
            "created_at": c.created_at,
        }
        for c in rows
    ]


@router.post("/circles")
async def upsert_circle(
    body: CircleUpsertRequest,
    principal: Principal = Depends(require_operator),
) -> dict[str, bool]:
    from spark.persistence.learning_repos import AuditRepository  # noqa: PLC0415

    async with session_scope() as session:
        row = await session.get(MemoryCircleRow, body.circle_id)
        if row is None:
            row = MemoryCircleRow(
                circle_id=body.circle_id,
                name=body.name,
                description=body.description,
            )
        else:
            row.name = body.name
            row.description = body.description
        session.add(row)
        await AuditRepository(session).append(
            actor=principal.subject,
            kind="memory.circle_upsert",
            target=body.circle_id,
            diff=body.model_dump(),
            severity="info",
        )
    return {"ok": True}


@router.post("/circles/{circle_id}/members")
async def add_circle_member(
    circle_id: str,
    body: CircleMembershipRequest,
    principal: Principal = Depends(require_operator),
) -> dict[str, bool]:
    from spark.persistence.learning_repos import AuditRepository  # noqa: PLC0415

    async with session_scope() as session:
        c = await session.get(MemoryCircleRow, circle_id)
        if c is None:
            raise HTTPException(status_code=404, detail="circle not found")
        stmt = select(CircleMembershipRow).where(
            CircleMembershipRow.circle_id == circle_id,
            CircleMembershipRow.agent_name == body.agent_name,
        )
        existing = (await session.execute(stmt)).scalars().first()
        if existing is not None:
            existing.can_read = body.can_read
            existing.can_write = body.can_write
            session.add(existing)
        else:
            session.add(
                CircleMembershipRow(
                    circle_id=circle_id,
                    agent_name=body.agent_name,
                    can_read=body.can_read,
                    can_write=body.can_write,
                )
            )
        await AuditRepository(session).append(
            actor=principal.subject,
            kind="memory.circle_member_upsert",
            target=f"{circle_id}:{body.agent_name}",
            diff=body.model_dump(),
            severity="info",
        )
    return {"ok": True}


@router.delete("/circles/{circle_id}/members/{agent_name}")
async def remove_circle_member(
    circle_id: str,
    agent_name: str,
    principal: Principal = Depends(require_operator),
) -> dict[str, bool]:
    async with session_scope() as session:
        stmt = select(CircleMembershipRow).where(
            CircleMembershipRow.circle_id == circle_id,
            CircleMembershipRow.agent_name == agent_name,
        )
        row = (await session.execute(stmt)).scalars().first()
        if row is None:
            raise HTTPException(status_code=404, detail="membership not found")
        await session.delete(row)
    return {"ok": True}


# ---------------------------------------------------------------------------
# Import / Export (T5.3)
# ---------------------------------------------------------------------------


@router.get("/long-term/export")
async def export_memories(
    namespace: str | None = None,
    principal: Principal = Depends(require_admin),
) -> list[dict[str, object]]:
    """Export memories as JSONL-friendly list (admin only, audited)."""
    from spark.persistence.learning_repos import AuditRepository  # noqa: PLC0415

    async with session_scope() as session:
        stmt = select(LongTermMemoryIndexRow)
        if namespace:
            stmt = stmt.where(LongTermMemoryIndexRow.namespace == namespace)
        rows = (await session.execute(stmt)).scalars().all()
        await AuditRepository(session).append(
            actor=principal.subject,
            kind="memory.export",
            target=namespace or "all",
            diff={"count": len(rows)},
            severity="elevated",
        )
    return [
        {
            "memory_id": r.memory_id,
            "agent_name": r.agent_name,
            "namespace": r.namespace,
            "memory_type": r.memory_type,
            "source_type": r.source_type,
            "sensitivity": r.sensitivity,
            "retention_class": r.retention_class,
            "confidence": r.confidence,
            "content_summary": r.content_summary,
            "tags": r.tags,
            "is_anti_pattern": r.is_anti_pattern,
            "valid_from": r.valid_from,
            "valid_until": r.valid_until,
            "provenance_json": r.provenance_json,
            "created_at": r.created_at,
        }
        for r in rows
    ]


@router.get("/visualize")
async def visualize_memories(
    namespace: str | None = None,
    limit: int = 500,
    _: Principal = Depends(require_viewer),
) -> dict[str, object]:
    """Project a namespace's memories into 2D via PCA for visualization.

    Uses a dependency-light PCA over the stored embeddings rather than
    UMAP (which would add a large dep). PCA loses some topology but is
    zero-dep and fast for < 1k memories. Returns {id, x, y, label, size}.
    """
    async with session_scope() as session:
        stmt = select(LongTermMemoryIndexRow).where(
            LongTermMemoryIndexRow.status == "active"
        )
        if namespace:
            stmt = stmt.where(LongTermMemoryIndexRow.namespace == namespace)
        stmt = stmt.limit(limit)
        rows = list((await session.execute(stmt)).scalars().all())

    if len(rows) < 3:
        return {"points": [], "reason": "not enough memories"}

    # Embed (or re-embed) summaries — cheap via SentenceTransformers.
    embedder = SentenceTransformersProvider()
    vectors = embedder.embed_batch([r.content_summary for r in rows])

    import math  # noqa: PLC0415

    # Manual 2-component PCA — no numpy dependency needed; embedding
    # dim is ~384, so this is O(n * d) covariance.
    n = len(vectors)
    d = len(vectors[0]) if n else 0
    # Center.
    mean = [sum(v[i] for v in vectors) / n for i in range(d)]
    centered = [[v[i] - mean[i] for i in range(d)] for v in vectors]

    # Power-iterate two principal components.
    def _normalize(v: list[float]) -> list[float]:
        norm = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / norm for x in v]

    def _matvec_T(mat: list[list[float]], vec: list[float]) -> list[float]:
        # Returns mat.T @ vec where mat is n x d (so result is d).
        return [
            sum(mat[i][j] * vec[i] for i in range(len(mat)))
            for j in range(len(mat[0]))
        ]

    def _matvec(mat: list[list[float]], vec: list[float]) -> list[float]:
        return [
            sum(row[j] * vec[j] for j in range(len(vec))) for row in mat
        ]

    def _power_iter(mat: list[list[float]], k: int = 20) -> list[float]:
        v = _normalize([1.0 / math.sqrt(d)] * d)
        for _ in range(k):
            proj = _matvec(mat, v)  # n
            v = _normalize(_matvec_T(mat, proj))  # d
        return v

    pc1 = _power_iter(centered)
    # Deflate and get PC2.
    proj1 = _matvec(centered, pc1)
    deflated = [
        [centered[i][j] - proj1[i] * pc1[j] for j in range(d)]
        for i in range(n)
    ]
    pc2 = _power_iter(deflated)

    xs = _matvec(centered, pc1)
    ys = _matvec(centered, pc2)

    # Normalize to [-1, 1].
    max_abs_x = max(abs(x) for x in xs) or 1.0
    max_abs_y = max(abs(y) for y in ys) or 1.0
    xs = [x / max_abs_x for x in xs]
    ys = [y / max_abs_y for y in ys]

    points = [
        {
            "id": rows[i].memory_id,
            "x": xs[i],
            "y": ys[i],
            "label": rows[i].content_summary[:80],
            "memory_type": rows[i].memory_type,
            "sensitivity": rows[i].sensitivity,
            "is_anti_pattern": bool(rows[i].is_anti_pattern),
            "namespace": rows[i].namespace,
            "confidence": rows[i].confidence,
            "citations": rows[i].successful_citation_count,
        }
        for i in range(n)
    ]
    return {"points": points}


def _resolve_chroma_path(agent) -> "Path":  # noqa: F821
    """Return the effective Chroma persist path, honoring data volume."""
    from pathlib import Path as _P  # noqa: PLC0415

    from spark.config.runtime_config import get_data_volume  # noqa: PLC0415

    dv = get_data_volume()
    if dv is not None:
        return dv.chroma_path
    ltm = agent.spec.memory.long_term_memory
    return _P(str(ltm.persist_path)).expanduser()


@router.get("/query")
async def query_long_term(
    namespace: str,
    q: str = Query(min_length=1, max_length=1000),
    top_k: int = 6,
    _: Principal = Depends(require_viewer),
) -> list[dict[str, object]]:
    ltm = LongTermMemory(
        namespace=namespace,
        collection_name=namespace,
        persist_path=Path("~/.spark/chroma"),
        embedder=SentenceTransformersProvider(),
    )
    try:
        hits = await retrieve(
            long_term=ltm,
            query=q,
            privacy_mode=PrivacyMode.STRICT,
            top_k=top_k,
        )
    except Exception as exc:  # pragma: no cover
        raise HTTPException(status_code=500, detail=f"query failed: {exc}") from exc
    return [
        {
            "memory_id": h.memory_id,
            "summary": h.summary,
            "memory_type": h.memory_type,
            "sensitivity": h.sensitivity,
            "confidence": h.confidence,
            "score": h.score,
        }
        for h in hits
    ]


@router.delete("/long-term/{memory_id}")
async def delete_long_term(
    memory_id: str, _: Principal = Depends(require_operator)
) -> dict[str, bool]:
    async with session_scope() as session:
        row = await session.get(LongTermMemoryIndexRow, memory_id)
        if row is None:
            raise HTTPException(status_code=404, detail="memory not found")
        ltm = LongTermMemory(
            namespace=row.namespace,
            collection_name=row.collection,
            persist_path=Path("~/.spark/chroma"),
            embedder=SentenceTransformersProvider(),
        )
        try:
            ltm.delete(memory_id)
        except Exception:  # pragma: no cover
            pass
        await session.delete(row)
    return {"ok": True}


@router.get("/pruning/status")
async def pruning_status(
    _: Principal = Depends(require_viewer),
) -> dict[str, object]:
    """Return the pruning config, next fire time, and the last run's counts."""
    runtime = load_runtime()
    cfg = runtime.spec.memory_pruning

    next_run: str | None = None
    try:
        from spark.scheduler import get_scheduler  # noqa: PLC0415

        sched = get_scheduler()
        if sched is not None:
            inner = getattr(sched, "_scheduler", None)
            if inner is not None:
                job = inner.get_job("spark:memory_pruning")
                if job is not None and job.next_run_time is not None:
                    from spark.utils.time import isoformat as _iso  # noqa: PLC0415

                    next_run = _iso(job.next_run_time)
    except Exception:  # pragma: no cover — best-effort, UI shows null
        next_run = None

    # Last-run data lives in audit_log with kind=memory.pruned.
    from spark.persistence.learning_models import AuditLogRow  # noqa: PLC0415

    last_run: dict[str, object] | None = None
    async with session_scope() as session:
        stmt = (
            select(AuditLogRow)
            .where(AuditLogRow.kind == "memory.pruned")
            .order_by(AuditLogRow.id.desc())
            .limit(1)
        )
        row = (await session.execute(stmt)).scalars().first()
        if row is not None:
            import json as _json  # noqa: PLC0415

            diff: dict[str, object] = {}
            if row.diff:
                try:
                    parsed = _json.loads(row.diff)
                    if isinstance(parsed, dict):
                        diff = parsed
                except ValueError:
                    diff = {}
            from spark.utils.time import isoformat as _iso_ts  # noqa: PLC0415

            last_run = {
                "at": _iso_ts(row.ts) if row.ts else None,
                "actor": row.actor,
                "total": diff.get("total", 0),
                "by_class": diff.get("by_class", {}),
                "namespaces": diff.get("namespaces", []),
                "dry_run": diff.get("dry_run", False),
            }

    return {
        "config": {
            "enabled": cfg.enabled,
            "schedule": cfg.schedule,
            "rollover_windows": {
                "temporary": cfg.rollover_windows.temporary,
                "expiring": cfg.rollover_windows.expiring,
                "review": cfg.rollover_windows.review,
                "persistent": cfg.rollover_windows.persistent,
            },
            "dry_run": cfg.dry_run,
            "notify_on_prune": cfg.notify_on_prune,
        },
        "next_run_at": next_run,
        "last_run": last_run,
    }


@router.post("/pruning/dry-run")
async def pruning_dry_run(
    principal: Principal = Depends(require_operator),
) -> dict[str, object]:
    """Trigger a dry-run sweep on demand. Writes counts to audit, no deletes."""
    cfg = load_runtime().spec.memory_pruning
    report = await run_memory_pruning_job(
        cfg, actor=f"user:{principal.name}", force_dry_run=True
    )
    return report.as_dict()


@router.post("/pruning/execute")
async def pruning_execute(
    principal: Principal = Depends(require_admin),
) -> dict[str, object]:
    """Trigger a real pruning sweep on demand. Admin only."""
    cfg = load_runtime().spec.memory_pruning
    report = await run_memory_pruning_job(
        cfg, actor=f"user:{principal.name}", force_dry_run=False
    )
    return report.as_dict()


@router.get("/playbooks/{agent_name}")
async def list_playbooks(
    agent_name: str, _: Principal = Depends(require_viewer)
) -> list[dict[str, object]]:
    async with session_scope() as session:
        result = await session.execute(
            select(PlaybookRow).where(PlaybookRow.agent_name == agent_name)
        )
        rows = list(result.scalars().all())
    return [
        {
            "playbook_id": r.playbook_id,
            "name": r.name,
            "description": r.description,
            "uses": r.uses,
            "alpha": r.alpha,
            "beta": r.beta,
            "success_rate": (r.alpha / (r.alpha + r.beta)) if (r.alpha + r.beta) > 0 else 0.5,
            "avg_duration_seconds": r.avg_duration_seconds,
            "avg_tool_calls": r.avg_tool_calls,
            "last_success_at": r.last_success_at,
            "tool_sequence": [t for t in r.tool_sequence.split("|") if t],
        }
        for r in rows
    ]
