"""Audit log browser."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from spark.persistence.db import session_scope
from spark.persistence.learning_repos import AuditRepository
from spark.web.auth import Principal, require_viewer
from spark.web.schemas import AuditEntry

router = APIRouter()


@router.get("/", response_model=list[AuditEntry])
async def list_audit(
    limit: int = 200,
    kind: str | None = None,
    min_severity: str | None = None,
    _: Principal = Depends(require_viewer),
) -> list[AuditEntry]:
    async with session_scope() as session:
        rows = await AuditRepository(session).list_recent(
            limit=limit, kind=kind, min_severity=min_severity
        )
    return [
        AuditEntry(
            ts=r.ts,
            actor=r.actor,
            kind=r.kind,
            target=r.target,
            diff=r.diff,
            reason=r.reason,
            severity=r.severity,
        )
        for r in rows
    ]
