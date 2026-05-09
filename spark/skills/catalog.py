"""Skill catalog — CRUD over approved + pending skills.

Approved skills live in `skills` (SQLite). The full `ApiSkill` JSON is stored
in `skill_reviews.payload_json` while pending; on approval we copy the relevant
fields into a `SkillRow` and also promote a distilled record into long-term
memory (Chroma) so retrieval can surface it alongside other memories.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from spark.config.enums import (
    MemoryType,
    PrivacyMode,
    RetentionClass,
    Sensitivity,
    SourceType,
)
from spark.memory.long_term import LongTermMemory
from spark.memory.promotion import MemoryCandidate, promote
from spark.persistence.db import session_scope
from spark.persistence.learning_models import SkillReviewRow, SkillRow
from spark.persistence.learning_repos import (
    AuditRepository,
    SkillRepository,
    SkillReviewRepository,
)
from spark.skills.schemas import ApiSkill, SkillReviewDecision
from spark.utils.hashing import sha256_text
from spark.utils.ids import short_id


@dataclass
class PendingSkill:
    review_id: str
    agent_name: str
    namespace: str
    skill: ApiSkill
    confidence: float
    discovered_at: str | None
    state: str
    reviewer: str | None = None


def _to_pending(row: SkillReviewRow) -> PendingSkill:
    try:
        skill = ApiSkill.model_validate_json(row.payload_json)
    except Exception:
        skill = ApiSkill(
            name=row.proposed_name,
            description=row.proposed_description or "",
            service_name=row.service_name,
            base_url=row.base_url,
            source_url=row.source_url,
        )
    return PendingSkill(
        review_id=row.review_id,
        agent_name=row.agent_name,
        namespace=row.namespace,
        skill=skill,
        confidence=row.confidence,
        discovered_at=row.discovered_at.isoformat() if row.discovered_at else None,
        state=row.state,
        reviewer=row.reviewer,
    )


class SkillCatalog:
    def __init__(self, *, long_term: LongTermMemory | None = None) -> None:
        self.long_term = long_term

    async def stage_for_review(
        self,
        *,
        agent_name: str,
        namespace: str,
        skill: ApiSkill,
    ) -> PendingSkill:
        review_id = f"skr-{short_id()}-{sha256_text(skill.source_url)[:10]}"
        row = SkillReviewRow(
            review_id=review_id,
            agent_name=agent_name,
            namespace=namespace,
            proposed_name=skill.name,
            proposed_description=skill.description,
            service_name=skill.service_name,
            base_url=skill.base_url,
            auth_method=skill.auth_method.value,
            required_secrets=",".join(skill.required_secrets),
            required_hosts=",".join(skill.required_hosts),
            source_url=skill.source_url,
            doc_hash=sha256_text(skill.source_url + skill.service_name),
            payload_json=skill.model_dump_json(),
            confidence=skill.confidence,
            state="pending",
        )
        async with session_scope() as session:
            repo = SkillReviewRepository(session)
            await repo.create(row)
            audit = AuditRepository(session)
            await audit.append(
                actor="agent",
                kind="skill.staged",
                target=f"{namespace}/{skill.name}",
                diff={"service": skill.service_name, "source": skill.source_url},
                reason=f"discovered from {skill.source_url}",
            )

        # Fire a HITL notification so the operator sees the pending review
        # surface in the bell badge.
        from spark.notifications import NotificationKind, get_notification_service

        await get_notification_service().notify(
            NotificationKind.HITL_SKILL_REVIEW,
            title=f"New skill pending review: {skill.name}",
            body=(
                f"Agent '{agent_name}' discovered a {skill.service_name} skill "
                f"from {skill.source_url}. Click to review."
            ),
            severity="elevated",
            target_kind="skill",
            target_id=review_id,
            action_url=f"/skills?focus={review_id}",
        )
        return _to_pending(row)

    async def list_pending(self, agent_name: str | None = None) -> list[PendingSkill]:
        async with session_scope() as session:
            repo = SkillReviewRepository(session)
            rows = await repo.list_pending(agent_name)
        return [_to_pending(r) for r in rows]

    async def list_approved_for_agent(self, agent_name: str) -> list[SkillRow]:
        async with session_scope() as session:
            repo = SkillRepository(session)
            return await repo.list_for_agent(agent_name)

    async def decide(
        self,
        decision: SkillReviewDecision,
        *,
        privacy_mode: PrivacyMode = PrivacyMode.STRICT,
    ) -> PendingSkill | None:
        async with session_scope() as session:
            repo = SkillReviewRepository(session)
            row = await repo.get(decision.review_id)
            if row is None:
                return None
            if row.state != "pending":
                return _to_pending(row)

            new_state = "approved" if decision.decision == "approve" else "rejected"
            skill = ApiSkill.model_validate_json(row.payload_json)
            if decision.final_name:
                skill = skill.model_copy(update={"name": decision.final_name})
            if decision.final_description:
                skill = skill.model_copy(
                    update={"description": decision.final_description}
                )

            resolved = await repo.resolve(
                decision.review_id,
                state=new_state,
                reviewer=decision.reviewer,
                notes=decision.notes,
            )
            assert resolved is not None

            audit = AuditRepository(session)
            await audit.append(
                actor=decision.reviewer,
                kind=f"skill.{new_state}",
                target=f"{row.namespace}/{row.proposed_name}",
                diff={"state": new_state, "notes": decision.notes},
                severity="elevated" if new_state == "approved" else "info",
            )

            if new_state == "approved":
                skill_id = f"sk-{short_id()}-{sha256_text(skill.source_url)[:10]}"
                skill_row = SkillRow(
                    skill_id=skill_id,
                    agent_name=row.agent_name,
                    namespace=row.namespace,
                    name=skill.name,
                    description=skill.description,
                    service_name=skill.service_name,
                    base_url=skill.base_url,
                    auth_method=skill.auth_method.value,
                    required_secrets=",".join(skill.required_secrets),
                    required_hosts=",".join(skill.required_hosts),
                    source_url=skill.source_url,
                    doc_hash=resolved.doc_hash,
                    confidence=skill.confidence,
                    approved_by=decision.reviewer,
                )
                skill_repo = SkillRepository(session)
                await skill_repo.upsert(skill_row)

                if self.long_term is not None:
                    try:
                        await promote(
                            long_term=self.long_term,
                            candidate=MemoryCandidate(
                                summary=f"Skill: {skill.name} — {skill.description}",
                                canonical_text=skill.compact_brief(),
                                memory_type=MemoryType.PATTERN,
                                source_type=SourceType.REFLECTION,
                                sensitivity=Sensitivity.LOW,
                                retention_class=RetentionClass.PERSISTENT,
                                confidence=skill.confidence,
                                tags=["skill", skill.service_name],
                            ),
                            agent_id=row.agent_name,
                            privacy_mode=privacy_mode,
                        )
                    except Exception:  # pragma: no cover
                        pass

        return _to_pending(resolved)
