"""propose_skill plugin — agent-side entry point for skill proposals.

Mirror image of the operator-side ``SkillsDiscoveryEngine`` (which crawls
API docs and stages skills automatically): this plugin lets the *agent*
formally propose a skill from inside its tool-call loop, instead of
just describing the idea in chat where it dies. The proposal lands in
the same ``skill_reviews`` queue and shows up on the Skills page;
operator approves or rejects with the existing UI.

Three flavors via the ``kind`` arg:

- ``api``       — external service integration (matches the existing
                  discovery-engine shape)
- ``behavior``  — meta-skill / heuristic: how the agent should approach
                  a class of problem (claim decomposition, source
                  scoring, structured verdict templates)
- ``knowledge`` — a domain fact or rule the agent wants the runtime to
                  surface back later via long-term-memory retrieval

Defenses against runaway proposals:

- ``enabled``                — operator master switch, default True
- ``max_pending_per_agent``  — refuses when the queue has too many for
                               this agent, default 20
- ``cooldown_seconds``       — same agent + same name within the
                               window is treated per ``dedupe_strategy``,
                               default 60
- ``dedupe_strategy``        — ``reject_duplicate`` (default) or
                               ``update_pending`` (overwrite the prior
                               pending row, useful when iterating)

Audit + notification fire on every successful proposal so the operator
sees the new pending row without polling.
"""

from __future__ import annotations

from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from spark.config.enums import Permission, Sensitivity


class ProposeSkillConfig(BaseModel):
    """Operator-controlled limits for agent-proposed skills."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(
        default=True,
        description="Master switch. Set False to refuse all proposals from agents.",
    )
    max_pending_per_agent: int = Field(
        default=20,
        ge=1,
        le=200,
        description=(
            "Per-agent ceiling on simultaneous pending proposals. "
            "Beyond this, propose_skill refuses with a clear error so "
            "the agent stops generating new ones until the operator "
            "drains the queue."
        ),
    )
    cooldown_seconds: int = Field(
        default=60,
        ge=0,
        le=86_400,
        description=(
            "If the agent re-proposes a skill with the same name within "
            "this window, dedupe_strategy applies. Set to 0 to disable."
        ),
    )
    dedupe_strategy: Literal["reject_duplicate", "update_pending"] = Field(
        default="reject_duplicate",
        description=(
            "reject_duplicate refuses an in-window dupe with a typed "
            "error. update_pending overwrites the prior pending row's "
            "payload so the agent can iterate on the proposal."
        ),
    )
    notify_on_proposal: bool = Field(
        default=True,
        description=(
            "Fire a HITL_SKILL_REVIEW notification when a new proposal "
            "lands so the operator's bell lights up immediately. Off "
            "if you'd rather drain the queue on a schedule."
        ),
    )


class ProposeSkillEndpoint(BaseModel):
    """One endpoint of an api-flavored skill (mirrors skills.SkillEndpoint)."""

    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=96, description="Endpoint label")
    method: Literal["GET", "POST", "PUT", "DELETE", "PATCH"] = Field(
        default="GET", description="HTTP method"
    )
    path: str = Field(min_length=1, max_length=512, description="URL path or template")
    description: str = Field(default="", max_length=1000, description="What it does")
    required_params: list[str] = Field(
        default_factory=list, description="Required query / body params"
    )
    optional_params: list[str] = Field(
        default_factory=list, description="Optional params"
    )
    example_request: str = Field(
        default="", max_length=2000, description="Example invocation"
    )
    rate_limit: str | None = Field(
        default=None, max_length=200, description="Per-endpoint rate-limit notes"
    )


class ProposeSkillArgs(BaseModel):
    """Agent-side proposal payload."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(
        min_length=1,
        max_length=128,
        description=(
            "Short slug-style label, e.g. 'claim_decomposition' or "
            "'serper_search'. Lowercase letters, digits, _, -, . only. "
            "The reviewer can rename it before approval."
        ),
    )
    description: str = Field(
        min_length=1,
        max_length=2000,
        description=(
            "What the skill does — one to three sentences a reviewer "
            "can scan quickly."
        ),
    )
    rationale: str = Field(
        min_length=1,
        max_length=2000,
        description=(
            "Why you (the agent) want this skill — what gap does it "
            "close, what failure mode does it prevent, what would you "
            "do differently with it. Required: vague proposals get "
            "rejected fast and waste review cycles."
        ),
    )
    kind: Literal["api", "behavior", "knowledge"] = Field(
        description=(
            "api = external service integration; behavior = how-to-think "
            "heuristic; knowledge = domain fact or rule. The reviewer "
            "page filters and renders by kind."
        )
    )

    # Behavior / knowledge flavor.
    examples: list[str] = Field(
        default_factory=list,
        max_length=8,
        description=(
            "Concrete usage examples (each up to 500 chars). REQUIRED "
            "for kind=behavior so the reviewer can judge the heuristic."
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

    # API flavor.
    service_name: str = Field(
        default="",
        max_length=128,
        description=(
            "External service name, e.g. 'GitHub Issues API'. REQUIRED "
            "for kind=api; ignored otherwise."
        ),
    )
    base_url: str = Field(
        default="",
        max_length=512,
        description=(
            "HTTPS base URL of the service. REQUIRED for kind=api; "
            "must be a real URL, not a placeholder."
        ),
    )
    auth_method: Literal[
        "none", "bearer", "api_key_header", "api_key_query", "basic", "oauth2"
    ] = Field(
        default="none",
        description="Auth scheme the service uses. Default 'none'.",
    )
    auth_secret_hint: str | None = Field(
        default=None,
        max_length=128,
        description=(
            "Suggested vault key the operator should provision "
            "(e.g. 'github_token'). Operator can override on approval."
        ),
    )
    required_hosts: list[str] = Field(
        default_factory=list,
        max_length=8,
        description=(
            "Hosts the operator must add to the agent's "
            "permissions.network.allow_hosts before this skill can be "
            "used. The reviewer sees this list when approving."
        ),
    )
    required_secrets: list[str] = Field(
        default_factory=list,
        max_length=8,
        description="Vault key names the skill needs at call time.",
    )
    endpoints: list[ProposeSkillEndpoint] = Field(
        default_factory=list,
        max_length=12,
        description=(
            "Per-endpoint method + path + param list. For kind=api, "
            "include at least one endpoint."
        ),
    )
    pricing_notes: str = Field(
        default="", max_length=1000, description="Cost notes for the operator."
    )
    rate_limit_notes: str = Field(
        default="", max_length=1000, description="Service-wide rate-limit notes."
    )
    source_url: str = Field(
        default="",
        max_length=1024,
        description=(
            "URL to the docs that informed the proposal, if any. "
            "Synthetic 'agent-proposal://...' is allowed for "
            "behavior/knowledge skills."
        ),
    )
    confidence: float = Field(
        default=0.5,
        description=(
            "How confident you are in this proposal (0..1). Clamped. "
            "Used by the review-page sort + bandit prior."
        ),
    )

    @field_validator("name")
    @classmethod
    def _slug(cls, v: str) -> str:
        import re as _re  # noqa: PLC0415

        if not _re.match(r"^[a-z0-9._-]+$", v):
            raise ValueError(
                "name must be lowercase letters, digits, '.', '_', or '-' only"
            )
        return v

    @field_validator("confidence")
    @classmethod
    def _clamp_confidence(cls, v: float) -> float:
        if v < 0.0:
            return 0.0
        if v > 1.0:
            return 1.0
        return v


class ProposeSkillResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    review_id: str
    state: str  # always "pending" on success
    dedupe_action: Literal["created", "updated_existing"]
    pending_count: int
    review_url: str  # relative path the operator can navigate to


class ProposeSkillPlugin:
    name: ClassVar[str] = "propose_skill"
    version: ClassVar[str] = "0.1.0"
    description: ClassVar[str] = (
        "Propose a new agent skill (api integration / behavior heuristic / "
        "knowledge rule) into the human-review queue. Operator-rate-limited; "
        "fails closed when ``enabled`` is false; fails with "
        "SKILL_QUEUE_FULL when too many proposals are pending; fails with "
        "VALIDATION when kind=api without a real base_url, or kind=behavior "
        "without examples."
    )
    input_schema: ClassVar[type[BaseModel]] = ProposeSkillArgs
    output_schema: ClassVar[type[BaseModel]] = ProposeSkillResult
    config_schema: ClassVar[type[BaseModel]] = ProposeSkillConfig
    # No external reach — DB write only. The agent allowlist + the
    # operator's `enabled` toggle are the gates.
    required_permissions: ClassVar[frozenset[Permission]] = frozenset()
    required_secrets: ClassVar[frozenset[str]] = frozenset()
    sensitivity: ClassVar[Sensitivity] = Sensitivity.LOW
    filter_output_before_model: ClassVar[bool] = True
    needs_network: ClassVar[bool] = False
    # Opt out of the bwrap sandbox: this plugin's only side effect is a
    # typed DB insert into the parent's SQLite (skill_reviews +
    # audit_log) plus an in-process notification fan-out. All paths the
    # plugin touches are bounded by Pydantic schemas and existing
    # repositories — no shell, no network, no filesystem writes outside
    # the runtime DB. The sandbox would either need write access to
    # spark.db (massively widening every other plugin's blast radius)
    # or a separate IPC channel to the parent for DB ops; in-process
    # execution is the smaller, simpler primitive.
    runs_in_sandbox: ClassVar[bool] = False

    async def execute(self, args: ProposeSkillArgs, ctx: Any) -> ProposeSkillResult:
        from datetime import UTC, datetime, timedelta  # noqa: PLC0415

        from spark.persistence.db import session_scope  # noqa: PLC0415
        from spark.persistence.learning_models import (  # noqa: PLC0415
            SkillReviewRow,
        )
        from spark.persistence.learning_repos import (  # noqa: PLC0415
            AuditRepository,
            SkillReviewRepository,
        )
        from spark.skills.schemas import ApiSkill, SkillKind  # noqa: PLC0415
        from spark.utils.hashing import sha256_text  # noqa: PLC0415
        from spark.utils.ids import short_id  # noqa: PLC0415

        cfg = getattr(ctx, "plugin_config", {}) or {}
        if not cfg.get("enabled", True):
            raise PermissionError(
                "propose_skill: operator has disabled skill proposals "
                "(set plugin_config.enabled = true to allow)"
            )
        max_pending = int(cfg.get("max_pending_per_agent") or 20)
        cooldown = int(cfg.get("cooldown_seconds") or 60)
        dedupe = str(cfg.get("dedupe_strategy") or "reject_duplicate")
        notify_on_proposal = bool(cfg.get("notify_on_proposal", True))

        # The agent name lives on the runtime context — the engine sets
        # it via plugin_config injection or directly on ctx. Fall back
        # to a sentinel so behavior is deterministic in tests.
        agent_name = (
            getattr(ctx, "agent_name", None)
            or cfg.get("__agent_name")
            or "unknown-agent"
        )

        # Cross-field validation by kind.
        if args.kind == "api":
            if not args.service_name or not args.base_url:
                raise PermissionError(
                    "propose_skill: kind=api requires service_name AND "
                    "base_url. Use kind=behavior or kind=knowledge for "
                    "non-API proposals."
                )
            if not args.base_url.lower().startswith(("http://", "https://")):
                raise PermissionError(
                    f"propose_skill: base_url {args.base_url!r} must "
                    "start with http:// or https://"
                )
        elif args.kind == "behavior":
            if not args.examples:
                raise PermissionError(
                    "propose_skill: kind=behavior requires at least one "
                    "concrete example so the reviewer can judge the "
                    "heuristic. Add 1-3 short examples and re-propose."
                )
        # kind=knowledge has no extra required fields beyond the base
        # name/description/rationale.

        # Synthesize sentinels for non-API kinds so the existing
        # ApiSkill schema accepts the payload without a separate table.
        service_name = args.service_name or f"{agent_name}:{args.kind}"
        base_url = args.base_url or f"agent-proposal://{agent_name}/{args.kind}"
        source_url = (
            args.source_url
            or f"agent-proposal://{agent_name}/{args.name}"
        )

        skill = ApiSkill(
            name=args.name,
            description=args.description,
            kind=SkillKind(args.kind),
            rationale=args.rationale,
            examples=list(args.examples),
            success_criteria=args.success_criteria,
            service_name=service_name,
            base_url=base_url,
            auth_method=args.auth_method,
            auth_secret_hint=args.auth_secret_hint,
            required_hosts=list(args.required_hosts),
            required_secrets=list(args.required_secrets),
            endpoints=[
                {
                    "name": e.name,
                    "method": e.method,
                    "path": e.path,
                    "description": e.description,
                    "required_params": list(e.required_params),
                    "optional_params": list(e.optional_params),
                    "example_request": e.example_request,
                    "rate_limit": e.rate_limit,
                }
                for e in args.endpoints
            ],
            pricing_notes=args.pricing_notes,
            rate_limit_notes=args.rate_limit_notes,
            source_url=source_url,
            confidence=args.confidence,
        )

        async with session_scope() as session:
            repo = SkillReviewRepository(session)
            audit = AuditRepository(session)

            pending_rows = await repo.list_pending(agent_name)

            # Dedupe: same agent + same name + still pending.
            existing = next(
                (r for r in pending_rows if r.proposed_name == args.name),
                None,
            )
            now = datetime.now(tz=UTC)
            if existing is not None and cooldown > 0:
                age = now - (existing.discovered_at or now)
                if age < timedelta(seconds=cooldown):
                    if dedupe == "reject_duplicate":
                        raise PermissionError(
                            f"propose_skill: a pending proposal named "
                            f"{args.name!r} already exists for agent "
                            f"{agent_name!r} (review_id={existing.review_id}); "
                            f"either wait {cooldown}s, switch the operator "
                            "config to dedupe_strategy=update_pending, "
                            "or pick a different name."
                        )
                    # update_pending — overwrite the existing row.
                    existing.proposed_description = args.description
                    existing.payload_json = skill.model_dump_json()
                    existing.confidence = args.confidence
                    existing.discovered_at = now
                    existing.required_secrets = ",".join(args.required_secrets)
                    existing.required_hosts = ",".join(args.required_hosts)
                    await audit.append(
                        actor=f"agent:{agent_name}",
                        kind="skill.proposal_updated",
                        target=f"{existing.review_id}/{args.name}",
                        diff={
                            "kind": args.kind,
                            "rationale": args.rationale[:200],
                        },
                        reason="agent re-proposed within cooldown window",
                        severity="info",
                    )
                    return ProposeSkillResult(
                        review_id=existing.review_id,
                        state="pending",
                        dedupe_action="updated_existing",
                        pending_count=len(pending_rows),
                        review_url="/skills",
                    )

            # Rate-limit: count current pending rows for this agent.
            if len(pending_rows) >= max_pending:
                raise PermissionError(
                    f"propose_skill: agent {agent_name!r} already has "
                    f"{len(pending_rows)} pending proposals (cap "
                    f"{max_pending}). Ask the operator to drain the "
                    "queue on the Skills page first."
                )

            review_id = f"skr-{short_id()}-{sha256_text(args.name)[:10]}"
            row = SkillReviewRow(
                review_id=review_id,
                agent_name=agent_name,
                namespace=f"agent-proposed/{args.kind}",
                proposed_name=args.name,
                proposed_description=args.description,
                service_name=service_name,
                base_url=base_url,
                auth_method=args.auth_method,
                required_secrets=",".join(args.required_secrets),
                required_hosts=",".join(args.required_hosts),
                source_url=source_url,
                doc_hash=sha256_text(f"{agent_name}:{args.name}:{args.kind}"),
                payload_json=skill.model_dump_json(),
                confidence=args.confidence,
                state="pending",
            )
            await repo.create(row)
            await audit.append(
                actor=f"agent:{agent_name}",
                kind="skill.proposed_by_agent",
                target=f"{review_id}/{args.name}",
                diff={
                    "kind": args.kind,
                    "rationale": args.rationale[:200],
                    "confidence": args.confidence,
                },
                reason=(
                    f"agent {agent_name} proposed kind={args.kind} skill "
                    f"{args.name} — needs review"
                ),
                severity="info",
            )

        # Fire the HITL_SKILL_REVIEW notification so the operator's bell
        # lights up. Outside the DB session because the notification
        # service uses its own connection and we don't want to hold the
        # write lock during the broker fan-out.
        if notify_on_proposal:
            try:
                from spark.notifications import (  # noqa: PLC0415
                    NotificationKind,
                    get_notification_service,
                )

                svc = get_notification_service()
                await svc.notify(
                    NotificationKind.HITL_SKILL_REVIEW,
                    title=f"New skill proposal: {args.name}",
                    body=(
                        f"{agent_name} proposed a {args.kind} skill: "
                        f"{args.description[:200]}"
                    ),
                    severity="info",
                    target_kind="skill_review",
                    target_id=review_id,
                    action_url="/skills",
                )
            except Exception:
                # Best-effort — proposal still landed in the DB even if
                # notification fan-out fails.
                pass

        return ProposeSkillResult(
            review_id=review_id,
            state="pending",
            dedupe_action="created",
            pending_count=len(pending_rows) + 1,
            review_url="/skills",
        )
