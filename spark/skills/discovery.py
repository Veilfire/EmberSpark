"""Skill discovery subgraph.

Given a capability gap (e.g., "I need to send a message via Telegram"), the
discovery pipeline:
  1. Asks the model which service and which doc host to consult.
  2. Verifies the host is in the trusted-doc allowlist.
  3. Uses the `http_client` plugin to fetch the doc page.
  4. Asks the model to extract a structured `ApiSkill` with
     `with_structured_output`.
  5. Stages the skill for human review via `SkillCatalog.stage_for_review`.

This module is careful to *never* auto-approve a skill. Discovery never writes
an approved `SkillRow` directly — that can only happen through `SkillCatalog.decide`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from spark.logging import get_logger
from spark.skills.catalog import PendingSkill, SkillCatalog
from spark.skills.schemas import ApiSkill
from spark.skills.sources import TrustedDocPolicy

log = get_logger("spark.skills.discovery")


class SkillDiscoveryError(RuntimeError):
    pass


class _DiscoveryPlan(BaseModel):
    """Model-produced plan before fetching anything."""

    model_config = ConfigDict(extra="forbid")

    service_name: str = Field(min_length=1, max_length=128)
    doc_host: str = Field(min_length=1, max_length=256)
    doc_url: str = Field(min_length=1, max_length=1024)
    reason: str = Field(default="", max_length=512)


@dataclass
class DiscoveryContext:
    agent_name: str
    namespace: str
    capability_gap: str
    trusted_policy: TrustedDocPolicy
    catalog: SkillCatalog
    chat_model: Any  # langchain BaseChatModel-ish
    http_call: Any   # async callable(url: str) -> (status, body)


class SkillDiscovery:
    """Runs a bounded research loop to produce a pending skill."""

    def __init__(self, ctx: DiscoveryContext) -> None:
        self.ctx = ctx

    async def run(self) -> PendingSkill:
        plan = await self._plan()
        log.info(
            "skill.plan",
            service=plan.service_name,
            doc_host=plan.doc_host,
            doc_url=plan.doc_url,
        )
        if not self.ctx.trusted_policy.allows(plan.doc_host):
            raise SkillDiscoveryError(
                f"doc host {plan.doc_host!r} not in trusted sources; operator must "
                f"add it via Security Center → Trusted Doc Sources"
            )

        status, body = await self.ctx.http_call(plan.doc_url)
        if status >= 400 or not body:
            raise SkillDiscoveryError(
                f"doc fetch failed: status={status} body_len={len(body) if body else 0}"
            )

        skill = await self._extract(plan, body)
        skill = skill.model_copy(update={"source_url": plan.doc_url})
        return await self.ctx.catalog.stage_for_review(
            agent_name=self.ctx.agent_name,
            namespace=self.ctx.namespace,
            skill=skill,
        )

    async def _plan(self) -> _DiscoveryPlan:
        system = (
            "You are the Spark skill discovery planner. "
            "Given a capability gap, propose ONE official documentation URL to consult. "
            "Only use well-known, canonical documentation hosts. "
            "Return a strict _DiscoveryPlan JSON."
        )
        user = f"Capability gap: {self.ctx.capability_gap}"
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        structured = self.ctx.chat_model.with_structured_output(_DiscoveryPlan)
        plan = await _invoke(structured, messages)
        if isinstance(plan, _DiscoveryPlan):
            return plan
        return _DiscoveryPlan.model_validate(plan)

    async def _extract(self, plan: _DiscoveryPlan, doc_body: str) -> ApiSkill:
        system = (
            "You are the Spark skill extractor. "
            "Given raw documentation text, produce a strict ApiSkill JSON. "
            "Be conservative: only populate fields you can verify from the text. "
            "Label the skill with a short `name` (<96 chars) and a 1-2 sentence "
            "`description` capturing what the skill lets the agent do. "
            f"service_name must be {plan.service_name!r}. "
            f"source_url must be {plan.doc_url!r}."
        )
        truncated = doc_body[:20_000]
        user = json.dumps({"doc_text": truncated}, default=str)
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        structured = self.ctx.chat_model.with_structured_output(ApiSkill)
        skill = await _invoke(structured, messages)
        if isinstance(skill, ApiSkill):
            return skill
        return ApiSkill.model_validate(skill)


async def _invoke(model: Any, messages: list[dict[str, Any]]) -> Any:
    if hasattr(model, "ainvoke"):
        return await model.ainvoke(messages)
    return model.invoke(messages)  # pragma: no cover — sync fallback
