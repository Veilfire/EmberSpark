"""Skill catalog + review queue routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select

from spark.persistence.db import session_scope
from spark.persistence.learning_models import SkillRow
from spark.skills.catalog import SkillCatalog
from spark.skills.schemas import SkillReviewDecision
from spark.web.auth import Principal, require_operator, require_viewer
from spark.web.schemas import PendingSkillView, SkillDecisionIn

router = APIRouter()


@router.get("/pending", response_model=list[PendingSkillView])
async def list_pending(
    agent_name: str | None = None,
    _: Principal = Depends(require_viewer),
) -> list[PendingSkillView]:
    catalog = SkillCatalog()
    pending = await catalog.list_pending(agent_name)
    return [
        PendingSkillView(
            review_id=p.review_id,
            agent_name=p.agent_name,
            namespace=p.namespace,
            proposed_name=p.skill.name,
            proposed_description=p.skill.description,
            kind=p.skill.kind.value,
            rationale=p.skill.rationale,
            examples=list(p.skill.examples),
            success_criteria=p.skill.success_criteria,
            service_name=p.skill.service_name,
            base_url=p.skill.base_url,
            auth_method=p.skill.auth_method.value,
            required_hosts=p.skill.required_hosts,
            required_secrets=p.skill.required_secrets,
            confidence=p.confidence,
            source_url=p.skill.source_url,
            discovered_at=None if p.discovered_at is None else _parse(p.discovered_at),
            state=p.state,
        )
        for p in pending
    ]


@router.get("/approved/{agent_name}")
async def list_approved(
    agent_name: str, _: Principal = Depends(require_viewer)
) -> list[dict[str, object]]:
    async with session_scope() as session:
        stmt = select(SkillRow).where(SkillRow.agent_name == agent_name)
        result = await session.execute(stmt)
        rows = list(result.scalars().all())
    return [
        {
            "skill_id": r.skill_id,
            "name": r.name,
            "description": r.description,
            "service_name": r.service_name,
            "base_url": r.base_url,
            "auth_method": r.auth_method,
            "required_hosts": [h for h in r.required_hosts.split(",") if h],
            "required_secrets": [s for s in r.required_secrets.split(",") if s],
            "confidence": r.confidence,
            "uses": r.uses,
            "status": r.status,
            "approved_by": r.approved_by,
            "approved_at": r.approved_at,
        }
        for r in rows
    ]


@router.post("/reviews/{review_id}")
async def decide_review(
    review_id: str,
    body: SkillDecisionIn,
    principal: Principal = Depends(require_operator),
) -> dict[str, str]:
    decision = SkillReviewDecision(
        review_id=review_id,
        decision=body.decision,
        reviewer=principal.subject,
        notes=body.notes,
        final_name=body.final_name,
        final_description=body.final_description,
    )
    catalog = SkillCatalog()
    result = await catalog.decide(decision)
    if result is None:
        raise HTTPException(status_code=404, detail="review not found")
    return {"state": result.state}


@router.post("/disable/{skill_id}")
async def disable_skill(
    skill_id: str, _: Principal = Depends(require_operator)
) -> dict[str, bool]:
    async with session_scope() as session:
        row = await session.get(SkillRow, skill_id)
        if row is None:
            raise HTTPException(status_code=404, detail="skill not found")
        row.status = "disabled"
    return {"ok": True}


def _parse(s: str) -> object:
    from datetime import datetime

    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None
