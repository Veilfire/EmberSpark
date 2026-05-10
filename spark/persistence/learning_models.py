"""Tables for the continuous-learning and skills subsystems.

- `playbooks` — named procedures the agent has used before, with bandit stats.
- `playbook_runs` — raw per-run observations that feed the bandit update.
- `skills` — approved skill records (the knowledge itself is also in Chroma).
- `skill_reviews` — pending skills awaiting human approval.
- `cost_events` — token/cost accounting per run (also feeds the UI dashboard).
- `audit_log` — immutable record of security-relevant mutations.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    return datetime.now(tz=UTC)


class PlaybookRow(SQLModel, table=True):
    __tablename__ = "playbooks"

    playbook_id: str = Field(primary_key=True, max_length=64)
    agent_name: str = Field(index=True, max_length=128)
    name: str = Field(max_length=128, index=True)
    description: str = ""
    fingerprint: str = Field(max_length=64, index=True)
    applicability_summary: str = ""
    tool_sequence: str = ""  # JSON: ["http_client", "markdown_writer"]
    # Bandit state (Beta posterior)
    alpha: float = 1.0  # successes + 1
    beta: float = 1.0   # failures + 1
    uses: int = 0
    last_success_at: datetime | None = None
    last_used_at: datetime | None = None
    avg_duration_seconds: float = 0.0
    avg_tool_calls: float = 0.0
    avg_model_calls: float = 0.0
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class PlaybookRunRow(SQLModel, table=True):
    __tablename__ = "playbook_runs"

    id: int | None = Field(default=None, primary_key=True)
    playbook_id: str = Field(index=True, max_length=64)
    run_id: str = Field(index=True, max_length=64)
    success: bool
    duration_seconds: float
    tool_calls: int
    model_calls: int
    recorded_at: datetime = Field(default_factory=_utcnow)


class SkillRow(SQLModel, table=True):
    """Approved, active skills. One row per skill; namespace-scoped."""

    __tablename__ = "skills"

    skill_id: str = Field(primary_key=True, max_length=64)
    agent_name: str = Field(index=True, max_length=128)
    namespace: str = Field(index=True, max_length=128)
    name: str = Field(max_length=128, index=True)
    description: str = ""
    service_name: str = Field(max_length=128, index=True)
    base_url: str = Field(max_length=512)
    auth_method: str = Field(max_length=64)
    required_secrets: str = ""  # CSV
    required_hosts: str = ""    # CSV — must be in agent network allowlist to USE
    source_url: str = Field(max_length=1024)
    doc_hash: str = Field(max_length=64)
    confidence: float = 0.5
    uses: int = 0
    last_used_at: datetime | None = None
    status: str = Field(default="approved", max_length=32)  # approved | disabled
    approved_by: str = Field(max_length=128)
    approved_at: datetime = Field(default_factory=_utcnow)
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class SkillReviewRow(SQLModel, table=True):
    """Pending skills awaiting human approval."""

    __tablename__ = "skill_reviews"

    review_id: str = Field(primary_key=True, max_length=64)
    agent_name: str = Field(index=True, max_length=128)
    namespace: str = Field(index=True, max_length=128)
    proposed_name: str = Field(max_length=128)
    proposed_description: str = ""
    service_name: str = Field(max_length=128)
    base_url: str = Field(max_length=512)
    auth_method: str = Field(max_length=64)
    required_secrets: str = ""
    required_hosts: str = ""
    source_url: str = Field(max_length=1024)
    doc_hash: str = Field(max_length=64)
    payload_json: str = ""  # full ApiSkill JSON
    state: str = Field(default="pending", max_length=32)  # pending | approved | rejected
    reviewer: str | None = None
    review_notes: str | None = None
    confidence: float = 0.5
    discovered_at: datetime = Field(default_factory=_utcnow)
    reviewed_at: datetime | None = None


class CostEventRow(SQLModel, table=True):
    __tablename__ = "cost_events"

    id: int | None = Field(default=None, primary_key=True)
    run_id: str = Field(index=True, max_length=64)
    agent_name: str = Field(index=True, max_length=128)
    task_name: str | None = Field(default=None, index=True, max_length=128)
    provider: str = Field(max_length=32)
    model: str = Field(max_length=128)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    prompt_cost_usd: float = 0.0
    completion_cost_usd: float = 0.0
    total_cost_usd: float = 0.0
    recorded_at: datetime = Field(default_factory=_utcnow)


class ModelCallEventRow(SQLModel, table=True):
    """Per-model-call telemetry — one row per planner iteration.

    The aggregated ``CostEventRow`` is recomputed from ``SUM(model_call_events)
    GROUP BY run_id`` at run-finalize so the existing Cost Dashboard keeps
    working unchanged. New per-call detail (cache hits, reasoning tokens,
    request_id deep-link to the provider dashboard) flows through this table.
    """

    __tablename__ = "model_call_events"

    id: int | None = Field(default=None, primary_key=True)
    run_id: str = Field(index=True, max_length=64)
    sequence: int = 0  # planner iteration index within the run
    started_at: datetime = Field(default_factory=_utcnow)
    finished_at: datetime | None = None
    latency_ms: int = 0
    provider: str = Field(max_length=32)
    model: str = Field(max_length=128)
    request_id: str | None = Field(default=None, max_length=128)
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0
    cache_creation_tokens: int = 0
    reasoning_tokens: int = 0
    cost_usd: float | None = None
    # ``computed`` (from PRICING_TABLE) or ``reported`` (provider authoritative).
    # OpenRouter rows start as ``computed`` and flip to ``reported`` once the
    # deferred /api/v1/generation enrichment lands.
    cost_source: str = Field(default="computed", max_length=16)
    raw_metadata_json: str = ""  # JSON dump of {usage_metadata, response_metadata}


class BudgetRow(SQLModel, table=True):
    __tablename__ = "budgets"

    budget_id: str = Field(primary_key=True, max_length=64)
    scope: str = Field(max_length=32)  # global | agent | provider
    scope_key: str = Field(max_length=128)  # agent name or provider name; "*" for global
    period: str = Field(max_length=16)  # daily | weekly | monthly
    limit_usd: float
    soft_alert_usd: float = 0.0
    hard_stop: bool = True
    enabled: bool = True
    updated_at: datetime = Field(default_factory=_utcnow)


class AuditLogRow(SQLModel, table=True):
    __tablename__ = "audit_log"

    id: int | None = Field(default=None, primary_key=True)
    ts: datetime = Field(default_factory=_utcnow, index=True)
    actor: str = Field(max_length=128)
    kind: str = Field(max_length=64, index=True)  # config.update, skill.approve, secret.rotate, etc.
    target: str = Field(max_length=256)
    diff: str = ""       # JSON diff
    reason: str = ""
    severity: str = Field(default="info", max_length=16)  # info | elevated | critical
    correlation_id: str | None = None


class GlobalPostureRow(SQLModel, table=True):
    """Singleton row holding the global safety posture (freeze, compliance mode)."""

    __tablename__ = "global_posture"

    id: int = Field(default=1, primary_key=True)
    frozen: bool = False
    freeze_reason: str = ""
    compliance_mode: str = Field(default="standard", max_length=16)  # standard | audit
    allow_internal_ips: bool = False
    allow_raw_logging: bool = False
    default_privacy_mode: str = Field(default="strict", max_length=16)
    updated_at: datetime = Field(default_factory=_utcnow)
    updated_by: str | None = None


class SessionSettingsRow(SQLModel, table=True):
    """Singleton row holding admin-editable session/auth settings.

    ``timeout_seconds`` is the authoritative session TTL. ``None`` means
    disabled — sessions do not expire. On startup the web layer reads this
    row and pushes it into ``AuthState``; PUT /api/settings/session
    updates both.
    """

    __tablename__ = "session_settings"

    id: int = Field(default=1, primary_key=True)
    timeout_seconds: int | None = None
    updated_at: datetime = Field(default_factory=_utcnow)
    updated_by: str | None = None


class TrustedDocSourceRow(SQLModel, table=True):
    __tablename__ = "trusted_doc_sources"

    host: str = Field(primary_key=True, max_length=256)
    added_by: str = Field(max_length=128)
    added_at: datetime = Field(default_factory=_utcnow)
    notes: str = ""


class InternalNetworkGrantRow(SQLModel, table=True):
    """Time-bound grant allowing an agent to reach an internal CIDR."""

    __tablename__ = "internal_network_grants"

    id: int | None = Field(default=None, primary_key=True)
    agent_name: str = Field(index=True, max_length=128)
    cidr: str = Field(max_length=64)
    reason: str
    granted_by: str = Field(max_length=128)
    granted_at: datetime = Field(default_factory=_utcnow)
    expires_at: datetime
    active: bool = True


class DataClassPolicyRow(SQLModel, table=True):
    """Operator-editable level per (scope_kind, agent, data_class).

    One row per override point. A ``global`` row applies to every agent
    unless a matching ``agent`` row exists. Deleting a row falls back to
    the built-in default from the taxonomy module.

    The ``mask_style`` / ``min_confidence`` / ``require_consensus`` /
    ``detector_overrides_json`` columns are populated by the Filtering
    page and consumed inside ``resolve_policy``. ``detector_overrides_json``
    is a free-form ``{detector_id: {enabled?: bool, threshold?: float}}``
    map; the redaction layer reads it lazily so unknown detector ids are
    silently skipped instead of raising.
    """

    __tablename__ = "data_class_policies"

    id: int | None = Field(default=None, primary_key=True)
    scope_kind: str = Field(max_length=16, index=True)  # "global" | "agent"
    agent_name: str | None = Field(default=None, index=True, max_length=128)
    data_class: str = Field(index=True, max_length=64)
    level: str = Field(max_length=16)  # allow | warn | redact | block
    scopes: str = Field(max_length=256)  # comma-sep DataScope values
    reason: str = ""
    mask_style: str | None = Field(default=None, max_length=32)  # MaskStyle.value
    min_confidence: float | None = None  # gate detector hits below this
    # Tri-state: None = inherit, True = require consensus, False = don't.
    # Stored as nullable INTEGER on SQLite.
    require_consensus: bool | None = None
    detector_overrides_json: str | None = None
    updated_at: datetime = Field(default_factory=_utcnow)
    updated_by: str | None = Field(default=None, max_length=128)


class FilteringPresetRow(SQLModel, table=True):
    """Named bundle of category settings — Strict / Balanced / Permissive / Custom.

    A preset is a JSON snapshot of every (data_class -> {level, scopes,
    mask_style, min_confidence, require_consensus, detector_overrides})
    pair. Applying a preset replaces matching ``DataClassPolicyRow``
    rows. Operators can fork a built-in preset by saving over it with a
    new name.
    """

    __tablename__ = "filtering_presets"

    name: str = Field(primary_key=True, max_length=64)
    description: str = ""
    builtin: bool = False  # True for the three shipped presets
    payload_json: str = "{}"
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)
    updated_by: str | None = Field(default=None, max_length=128)


class DataClassGrantRow(SQLModel, table=True):
    """Explicit carve-out that bypasses the policy hierarchy for one class.

    E.g. an agent whose job is credit-card processing gets a single
    ``financial.card`` grant with ``level_override=allow`` covering the
    relevant scopes. Grants are the only mechanism that can raise the
    effective level *above* the resolved policy.
    """

    __tablename__ = "data_class_grants"

    id: int | None = Field(default=None, primary_key=True)
    agent_name: str = Field(index=True, max_length=128)  # "__all__" for global
    data_class: str = Field(index=True, max_length=64)
    scopes: str = Field(max_length=256)
    level_override: str = Field(default="allow", max_length=16)
    reason: str
    granted_by: str = Field(max_length=128)
    granted_at: datetime = Field(default_factory=_utcnow)
    expires_at: datetime | None = None  # null = permanent (audited critical)
    active: bool = True


# ---------------------------------------------------------------------------
# F1 — Plugin Configuration System
# ---------------------------------------------------------------------------


class PluginConfigRow(SQLModel, table=True):
    """One row per built-in plugin; operator-edited config.

    The config is JSON-serialized because each plugin has its own Pydantic
    schema. `schema_hash` is the sha256 of the schema JSON at save time so
    the UI can detect drift after a plugin upgrade.
    """

    __tablename__ = "plugin_configs"

    plugin_name: str = Field(primary_key=True, max_length=128)
    config_json: str = Field(default="{}")
    schema_hash: str = Field(max_length=64)
    updated_by: str | None = None
    updated_at: datetime = Field(default_factory=_utcnow)


# ---------------------------------------------------------------------------
# F2 — Persona Manager
# ---------------------------------------------------------------------------


class PersonaRow(SQLModel, table=True):
    """Named system-prompt persona. Exactly one row has `is_active=True`."""

    __tablename__ = "agent_personas"

    persona_id: str = Field(primary_key=True, max_length=64)
    name: str = Field(index=True, max_length=128)
    description: str = ""
    system_prompt: str = ""
    tone: str | None = None
    tags: str | None = None  # CSV
    is_active: bool = Field(default=False, index=True)
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


# ---------------------------------------------------------------------------
# F4 — Run span tracing
# ---------------------------------------------------------------------------


class RunSpanRow(SQLModel, table=True):
    """A span recorded by `spark.runtime.spans.Span`."""

    __tablename__ = "run_spans"

    id: int | None = Field(default=None, primary_key=True)
    run_id: str = Field(index=True, max_length=64)
    parent_span_id: int | None = Field(default=None, index=True)
    name: str = Field(max_length=128)
    started_at: datetime
    finished_at: datetime | None = None
    duration_ms: float | None = None
    attributes: str = ""  # JSON
    error_class: str | None = Field(default=None, max_length=64)


# ---------------------------------------------------------------------------
# F5 — Webhook triggers
# ---------------------------------------------------------------------------


class TriggerRow(SQLModel, table=True):
    """A named webhook trigger bound to a task.

    Two auth modes:

    - ``bearer``: caller sends a token in ``X-Spark-Token``; verified
      against the bcrypt-hashed ``token_hash``. Cleartext is shown to
      the operator exactly once at creation time.
    - ``hmac_sha256``: caller sends a body and a signature header
      (``X-Hub-Signature-256: sha256=<hex>``). The shared secret lives
      in the age vault under ``secret_name`` because HMAC verification
      requires the original cleartext, not a hash.

    ``payload_forwarding`` controls whether the inbound request body is
    captured into ``RunState.trigger_payload`` and rendered into the
    planner's first system prompt. ``event_filter_json`` is a small dict
    of dotted-path → expected-value checks applied to the body before
    firing (e.g. ``{"action": "closed", "pull_request.merged": true}``
    for "fire only on merged PRs").

    ``failed_verify_count`` + ``locked_until`` implement defence in
    depth against credential-stuffing on a leaked endpoint URL.
    """

    __tablename__ = "triggers"

    trigger_id: str = Field(primary_key=True, max_length=64)
    task_name: str = Field(index=True, max_length=128)
    token_hash: str = Field(max_length=128, default="")  # bearer mode only
    # auth_mode controls verification:
    #   ``bearer``           — X-Spark-Token compared via bcrypt
    #   ``hmac_sha256``      — X-Hub-Signature-256 / X-Spark-Signature-256
    #                          over the raw body. GitHub, Stripe, Linear,
    #                          Vercel, Twilio etc all use this.
    #   ``hmac_sha256_slack`` — X-Slack-Signature over ``v0:<ts>:<body>``
    #                          with replay-window check (5 min).
    auth_mode: str = Field(default="bearer", max_length=32)
    secret_name: str | None = Field(default=None, max_length=128)
    # ``json`` (default), ``form`` (application/x-www-form-urlencoded —
    # Twilio, Stripe legacy), or ``raw`` (passthrough; the agent gets a
    # ``{"raw": "<utf8>"}`` payload). Affects how event_filter sees the body.
    body_parser: str = Field(default="json", max_length=16)
    payload_forwarding: bool = False
    event_filter_json: str | None = None
    failed_verify_count: int = 0
    locked_until: datetime | None = None
    enabled: bool = True
    rate_limit_per_hour: int = 60
    created_at: datetime = Field(default_factory=_utcnow)
    last_fired_at: datetime | None = None
    fires_total: int = 0


# ---------------------------------------------------------------------------
# F6 — Annotations and incident acks
# ---------------------------------------------------------------------------


class AnnotationRow(SQLModel, table=True):
    """Operator-authored markdown notes attached to any runtime entity."""

    __tablename__ = "annotations"

    id: int | None = Field(default=None, primary_key=True)
    kind: str = Field(index=True, max_length=32)  # run | memory | skill | persona | plugin
    target_id: str = Field(index=True, max_length=128)
    body: str
    author: str = Field(max_length=128)
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class IncidentAckRow(SQLModel, table=True):
    """Acknowledgment state for a critical audit entry."""

    __tablename__ = "incident_acks"

    audit_id: int = Field(primary_key=True)
    acked_by: str = Field(max_length=128)
    acked_at: datetime = Field(default_factory=_utcnow)
    note: str | None = None


# ---------------------------------------------------------------------------
# G3 — Notifications
# ---------------------------------------------------------------------------


class NotificationRow(SQLModel, table=True):
    """One notification row.

    Every HITL / download / incident signal lands here and fans out to the
    SSE bus for the web UI's bell + drawer. Dismissed rows are kept for
    audit; read rows are kept until the operator bulk-clears.
    """

    __tablename__ = "notifications"

    id: int | None = Field(default=None, primary_key=True)
    kind: str = Field(index=True, max_length=64)
    severity: str = Field(default="info", max_length=16)  # info | elevated | critical
    title: str = Field(max_length=200)
    body: str | None = None
    target_kind: str | None = Field(default=None, max_length=32)
    target_id: str | None = Field(default=None, max_length=256)
    action_url: str | None = Field(default=None, max_length=512)
    created_at: datetime = Field(default_factory=_utcnow, index=True)
    read_at: datetime | None = Field(default=None, index=True)
    dismissed_at: datetime | None = None


class NotificationPreferencesRow(SQLModel, table=True):
    """Singleton row — per-kind notification toggles.

    There is only ever one row (id=1). Matches the single-operator model.
    Each boolean is a per-kind opt-in/opt-out. ``True`` means notify.
    """

    __tablename__ = "notification_preferences"

    id: int = Field(default=1, primary_key=True)
    download_ready: bool = True
    hitl_skill_review: bool = True
    hitl_approval: bool = True
    hitl_dlq: bool = True
    ip_grant_expiring: bool = True
    raw_logging_on: bool = True
    cost_soft_alert: bool = True
    cost_hard_stop: bool = True
    incident: bool = True
    plugin_hash_changed: bool = True
    memory_pruned: bool = True
    memory_contradiction: bool = True
    memory_review_needed: bool = True
    data_class_blocked: bool = True
    data_class_grant_expiring: bool = True
    # Gate-family kinds — one per family, see ``NotificationKind.GATE_*``.
    # Default on so operators see denials by default; very chatty gates
    # are deduped per (agent, code, target) over a 5-minute window so a
    # tight loop doesn't flood the bell.
    gate_permission_denied: bool = True
    gate_budget_exceeded: bool = True
    gate_network_denied: bool = True
    gate_filesystem_denied: bool = True
    gate_sandbox_failed: bool = True
    play_sound: bool = False
    toast_on_create: bool = True
    updated_at: datetime = Field(default_factory=_utcnow)


# ---------------------------------------------------------------------------
# H2 — Forensic review
# ---------------------------------------------------------------------------


class ForensicCaptureRow(SQLModel, table=True):
    """One opt-in forensic capture for a task run.

    The payload bytes live in :class:`ForensicSnapshotRow`. This row
    holds the per-run metadata + the secrets-provider key under which
    the per-run age identity is stored. Deleting the identity (via
    ``SecretManager.delete``) cryptographically shreds the data before
    the rows themselves are dropped.
    """

    __tablename__ = "forensic_captures"

    run_id: str = Field(primary_key=True, max_length=64)
    agent_name: str = Field(index=True, max_length=128)
    task_name: str = Field(index=True, max_length=128)
    enabled_by: str = Field(max_length=128)
    enabled_reason: str = Field(max_length=500)
    captured_at: datetime = Field(default_factory=_utcnow, index=True)
    expires_at: datetime = Field(index=True)
    # Secrets-provider key holding the per-run age identity. Delete this
    # secret and the snapshots become permanently unreadable.
    vault_key: str = Field(max_length=128)
    iteration_count: int = 0
    snapshot_count: int = 0
    wiped_at: datetime | None = None


class ForensicSnapshotRow(SQLModel, table=True):
    """One encrypted payload in the forensic chain.

    ``kind`` is one of ``prompt``, ``model``, ``tool``,
    ``memory_retrieved``, ``memory_written``, ``reflection``. The
    payload is age-encrypted JSON matching one of the Pydantic schemas
    in :mod:`spark.forensic.schemas`.
    """

    __tablename__ = "forensic_snapshots"

    id: int | None = Field(default=None, primary_key=True)
    run_id: str = Field(index=True, max_length=64, foreign_key="forensic_captures.run_id")
    iteration: int = Field(default=0, index=True)
    sequence: int = Field(default=0)
    span_id: int | None = None
    kind: str = Field(max_length=32, index=True)
    captured_at: datetime = Field(default_factory=_utcnow)
    payload_encrypted: bytes
