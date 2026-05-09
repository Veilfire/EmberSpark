"""Operator notes attached to runs / memories / skills / personas / plugins."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from spark.persistence.db import session_scope
from spark.persistence.learning_models import AnnotationRow
from spark.persistence.learning_repos import AnnotationRepository
from spark.web.auth import Principal, require_operator, require_viewer

router = APIRouter()


class AnnotationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["run", "memory", "skill", "persona", "plugin"]
    target_id: str = Field(min_length=1, max_length=128)
    body: str = Field(min_length=1, max_length=16_000)


class AnnotationView(BaseModel):
    id: int
    kind: str
    target_id: str
    body: str
    author: str
    created_at: datetime
    updated_at: datetime


@router.get("/")
async def list_notes(
    kind: str, target_id: str, _: Principal = Depends(require_viewer)
) -> list[AnnotationView]:
    async with session_scope() as session:
        rows = await AnnotationRepository(session).list_for(kind=kind, target_id=target_id)
    return [
        AnnotationView(
            id=r.id or 0,
            kind=r.kind,
            target_id=r.target_id,
            body=r.body,
            author=r.author,
            created_at=r.created_at,
            updated_at=r.updated_at,
        )
        for r in rows
    ]


@router.post("/", response_model=AnnotationView)
async def create_note(
    body: AnnotationCreate, principal: Principal = Depends(require_operator)
) -> AnnotationView:
    async with session_scope() as session:
        row = AnnotationRow(
            kind=body.kind,
            target_id=body.target_id,
            body=body.body,
            author=principal.subject,
        )
        await AnnotationRepository(session).append(row)
        await session.flush()
    return AnnotationView(
        id=row.id or 0,
        kind=row.kind,
        target_id=row.target_id,
        body=row.body,
        author=row.author,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@router.delete("/{annotation_id}")
async def delete_note(
    annotation_id: int, _: Principal = Depends(require_operator)
) -> dict[str, bool]:
    async with session_scope() as session:
        removed = await AnnotationRepository(session).delete(annotation_id)
    if not removed:
        raise HTTPException(status_code=404, detail="annotation not found")
    return {"ok": True}
