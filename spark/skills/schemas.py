"""Pydantic schemas for skill records."""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class SkillAuthMethod(str, Enum):
    NONE = "none"
    BEARER = "bearer"
    API_KEY_HEADER = "api_key_header"
    API_KEY_QUERY = "api_key_query"
    BASIC = "basic"
    OAUTH2 = "oauth2"


class SkillEndpoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=96)
    method: Literal["GET", "POST", "PUT", "DELETE", "PATCH"] = "GET"
    path: str = Field(min_length=1, max_length=512)
    description: str = Field(default="", max_length=1000)
    required_params: list[str] = Field(default_factory=list)
    optional_params: list[str] = Field(default_factory=list)
    example_request: str = Field(default="", max_length=2000)
    rate_limit: str | None = None


class SkillKind(str, Enum):
    """What flavor of skill this is.

    - ``api`` — an external API integration: the agent learns how to talk
      to a specific service (base_url, auth, endpoints). This is what
      the discovery engine produces by crawling docs.
    - ``behavior`` — a meta-skill / heuristic about *how* the agent should
      operate (claim decomposition, source quality scoring, structured
      verdict templates). No external service involved.
    - ``knowledge`` — a recalled fact or domain rule the agent wants the
      runtime to surface back on future runs (closer to a long-term
      memory entry, but routed through the human-review gate).
    """

    API = "api"
    BEHAVIOR = "behavior"
    KNOWLEDGE = "knowledge"


class ApiSkill(BaseModel):
    """A structured description of an agent capability awaiting review.

    Originally just for API integrations (what the discovery engine
    produces); now also carries agent-proposed *behavior* and *knowledge*
    skills via the ``propose_skill`` plugin. ``kind`` distinguishes the
    flavor; the API-only fields (``service_name``, ``base_url``,
    ``endpoints``) are sentinel-filled for behavior/knowledge skills so
    the existing storage + review schema accepts them without a new
    table.

    The AI labeler populates ``name`` and ``description`` on discovery;
    the reviewer can edit them before approval.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=128, description="Short label")
    description: str = Field(min_length=1, max_length=2000)
    # ``api`` for backward compat — every existing discovery-flow skill
    # is an API skill. New agent-proposed skills set this explicitly.
    kind: SkillKind = SkillKind.API
    rationale: str = Field(
        default="",
        max_length=2000,
        description=(
            "Why the proposer thinks this skill is worth approving — "
            "surfaced verbatim to the reviewer. Required by the "
            "propose_skill plugin; empty for discovery-flow skills."
        ),
    )
    examples: list[str] = Field(
        default_factory=list,
        max_length=8,
        description=(
            "Concrete usage examples (one per item, ~500 chars each). "
            "Required for kind=behavior so the reviewer can judge "
            "the heuristic; ignored for kind=api."
        ),
    )
    success_criteria: str = Field(
        default="",
        max_length=1000,
        description=(
            "How the reviewer or future-you knows the skill is "
            "actually working. Optional but encouraged."
        ),
    )
    service_name: str = Field(min_length=1, max_length=128)
    base_url: str = Field(max_length=512)
    auth_method: SkillAuthMethod = SkillAuthMethod.NONE
    auth_secret_hint: str | None = Field(
        default=None,
        description="Suggested secret name (e.g. 'telegram_bot_token')",
    )
    required_hosts: list[str] = Field(
        default_factory=list,
        description="Hosts the agent must have in its network allowlist to use this skill",
    )
    required_secrets: list[str] = Field(default_factory=list)
    endpoints: list[SkillEndpoint] = Field(default_factory=list)
    pricing_notes: str = Field(default="", max_length=1000)
    rate_limit_notes: str = Field(default="", max_length=1000)
    source_url: str = Field(max_length=1024)
    # See ``MemoryCandidatePayload.confidence`` for why we clamp via a
    # validator instead of ``Field(ge=0, le=1)`` — Bedrock's tool-calling
    # JSON Schema subset rejects ``minimum``/``maximum`` on ``number``
    # types, and this schema is fed to ``with_structured_output``.
    confidence: float = 0.5

    @field_validator("confidence")
    @classmethod
    def _clamp_confidence(cls, v: float) -> float:
        if v < 0.0:
            return 0.0
        if v > 1.0:
            return 1.0
        return v

    def compact_brief(self) -> str:
        lines = [
            f"Skill: {self.name} ({self.kind.value})",
            f"Description: {self.description}",
        ]
        if self.kind == SkillKind.API:
            lines.append(f"Service: {self.service_name}")
            lines.append(f"Base URL: {self.base_url}")
            lines.append(f"Auth: {self.auth_method.value}")
            if self.required_hosts:
                lines.append(f"Required hosts: {', '.join(self.required_hosts)}")
            if self.required_secrets:
                lines.append(f"Required secrets: {', '.join(self.required_secrets)}")
            if self.endpoints:
                lines.append("Endpoints:")
                for e in self.endpoints:
                    lines.append(f"  - {e.method} {e.path} — {e.description[:80]}")
        else:
            if self.rationale:
                lines.append(f"Rationale: {self.rationale}")
            if self.examples:
                lines.append("Examples:")
                for ex in self.examples[:3]:
                    lines.append(f"  - {ex[:200]}")
            if self.success_criteria:
                lines.append(f"Success criteria: {self.success_criteria}")
        return "\n".join(lines)


class SkillReviewDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    review_id: str
    decision: Literal["approve", "reject"]
    reviewer: str
    notes: str | None = None
    final_name: str | None = None
    final_description: str | None = None
