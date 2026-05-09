"""Persona manager routes.

Persona edits take effect on the very next model call — the engine re-reads
the active persona from the DB on every iteration. See
``spark.runtime.engine.RuntimeEngine._system_prompt``.
"""

from __future__ import annotations

import secrets as _secrets
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from spark.persistence.db import session_scope
from spark.persistence.learning_models import PersonaRow
from spark.persistence.learning_repos import AuditRepository, PersonaRepository
from spark.web.auth import Principal, require_operator, require_viewer

router = APIRouter()

MAX_SYSTEM_PROMPT_CHARS = 64 * 1024


class PersonaCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=128)
    description: str = Field(default="", max_length=1000)
    system_prompt: str = Field(min_length=1, max_length=MAX_SYSTEM_PROMPT_CHARS)
    tone: str | None = Field(default=None, max_length=256)
    tags: list[str] = Field(default_factory=list)


class PersonaUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=1000)
    system_prompt: str | None = Field(
        default=None, min_length=1, max_length=MAX_SYSTEM_PROMPT_CHARS
    )
    tone: str | None = Field(default=None, max_length=256)
    tags: list[str] | None = None


class PersonaView(BaseModel):
    persona_id: str
    name: str
    description: str
    system_prompt: str
    tone: str | None
    tags: list[str]
    is_active: bool
    created_at: datetime
    updated_at: datetime


class PersonaPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    objective: str = Field(default="", max_length=4000)


def _view(row: PersonaRow) -> PersonaView:
    return PersonaView(
        persona_id=row.persona_id,
        name=row.name,
        description=row.description,
        system_prompt=row.system_prompt,
        tone=row.tone,
        tags=[t for t in (row.tags or "").split(",") if t],
        is_active=row.is_active,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _tags_csv(tags: list[str] | None) -> str:
    return ",".join(t.strip() for t in (tags or []) if t.strip())


@router.get("/", response_model=list[PersonaView])
async def list_personas(_: Principal = Depends(require_viewer)) -> list[PersonaView]:
    async with session_scope() as session:
        rows = await PersonaRepository(session).list_all()
    return [_view(r) for r in rows]


@router.get("/active", response_model=PersonaView | None)
async def get_active(_: Principal = Depends(require_viewer)) -> PersonaView | None:
    async with session_scope() as session:
        row = await PersonaRepository(session).get_active()
    return _view(row) if row is not None else None


@router.get("/{persona_id}", response_model=PersonaView)
async def get_persona(
    persona_id: str, _: Principal = Depends(require_viewer)
) -> PersonaView:
    async with session_scope() as session:
        row = await PersonaRepository(session).get(persona_id)
    if row is None:
        raise HTTPException(status_code=404, detail="persona not found")
    return _view(row)


@router.post("/", response_model=PersonaView)
async def create_persona(
    body: PersonaCreate, principal: Principal = Depends(require_operator)
) -> PersonaView:
    persona_id = f"pers-{_secrets.token_hex(6)}"
    row = PersonaRow(
        persona_id=persona_id,
        name=body.name,
        description=body.description,
        system_prompt=body.system_prompt,
        tone=body.tone,
        tags=_tags_csv(body.tags),
        is_active=False,
    )
    async with session_scope() as session:
        await PersonaRepository(session).upsert(row)
        await AuditRepository(session).append(
            actor=principal.subject,
            kind="persona.created",
            target=persona_id,
            diff={"name": body.name},
            severity="info",
        )
    return _view(row)


@router.put("/{persona_id}", response_model=PersonaView)
async def update_persona(
    persona_id: str,
    body: PersonaUpdate,
    principal: Principal = Depends(require_operator),
) -> PersonaView:
    async with session_scope() as session:
        repo = PersonaRepository(session)
        row = await repo.get(persona_id)
        if row is None:
            raise HTTPException(status_code=404, detail="persona not found")
        if body.name is not None:
            row.name = body.name
        if body.description is not None:
            row.description = body.description
        if body.system_prompt is not None:
            row.system_prompt = body.system_prompt
        if body.tone is not None:
            row.tone = body.tone
        if body.tags is not None:
            row.tags = _tags_csv(body.tags)
        await repo.upsert(row)
        await AuditRepository(session).append(
            actor=principal.subject,
            kind="persona.updated",
            target=persona_id,
            diff=body.model_dump(exclude_none=True),
            severity="info",
        )
    return _view(row)


@router.post("/{persona_id}/activate", response_model=PersonaView)
async def activate_persona(
    persona_id: str, principal: Principal = Depends(require_operator)
) -> PersonaView:
    async with session_scope() as session:
        repo = PersonaRepository(session)
        row = await repo.activate(persona_id)
        if row is None:
            raise HTTPException(status_code=404, detail="persona not found")
        await AuditRepository(session).append(
            actor=principal.subject,
            kind="persona.activated",
            target=persona_id,
            diff={"name": row.name},
            severity="elevated",
        )
    return _view(row)


@router.delete("/{persona_id}")
async def delete_persona(
    persona_id: str, principal: Principal = Depends(require_operator)
) -> dict[str, bool]:
    async with session_scope() as session:
        try:
            removed = await PersonaRepository(session).delete(persona_id)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if removed:
            await AuditRepository(session).append(
                actor=principal.subject,
                kind="persona.deleted",
                target=persona_id,
                severity="info",
            )
    return {"ok": removed}


@router.post("/{persona_id}/preview")
async def preview(
    persona_id: str,
    body: PersonaPreviewRequest,
    _: Principal = Depends(require_viewer),
) -> dict[str, Any]:
    """Render what the model would see as its system message.

    Shows the raw assembled prompt for the persona in isolation (without
    retrieved memories / playbook / skills). Useful for quick tuning.
    """
    async with session_scope() as session:
        row = await PersonaRepository(session).get(persona_id)
    if row is None:
        raise HTTPException(status_code=404, detail="persona not found")
    pieces = [row.system_prompt.strip()]
    if row.tone:
        pieces.append(f"Tone: {row.tone.strip()}")
    pieces.append("You operate under strict budgets and a plugin allowlist.")
    pieces.append("Privacy mode: strict.")
    pieces.append(
        "Respond with a JSON tool call object `{\"tool\": \"name\", \"args\": {...}}` "
        "when you need to invoke a plugin, otherwise respond with the final answer."
    )
    assembled = "\n".join(pieces)
    return {
        "system_prompt": assembled,
        "user_objective": body.objective,
        "char_count": len(assembled),
    }
