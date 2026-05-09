"""Request/response Pydantic models for the web API."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer

from spark.config.enums import PrivacyMode, SandboxBackend


def _iso_utc(dt: datetime | None) -> str | None:
    """Serialize datetime as UTC ISO-8601 with 'Z'.

    Naive datetimes (SQLite round-trips drop tzinfo) are assumed to be UTC —
    every Spark row is written via `datetime.now(tz=timezone.utc)`.
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.isoformat().replace("+00:00", "Z")


UtcDatetime = Annotated[
    datetime,
    PlainSerializer(_iso_utc, return_type=str, when_used="json"),
]
OptUtcDatetime = Annotated[
    datetime | None,
    PlainSerializer(_iso_utc, return_type=str | None, when_used="json"),
]


class _Resp(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---- scheduler / tasks ----


class AgentSummary(_Resp):
    name: str
    description: str
    updated_at: UtcDatetime


class TaskSummary(_Resp):
    name: str
    agent_name: str
    mode: str
    state: str
    created_at: UtcDatetime
    updated_at: UtcDatetime


class TaskRunSummary(_Resp):
    run_id: str
    task_name: str
    agent_name: str
    state: str
    started_at: UtcDatetime
    finished_at: OptUtcDatetime
    iterations: int
    model_calls: int
    tool_calls: int
    summary: str | None
    error: str | None


class TaskTriggerRequest(_Resp):
    task_name: str
    agent_name: str


# ---- chat ----


class ChatMessageIn(_Resp):
    session_id: str
    agent_name: str
    content: str = Field(min_length=1, max_length=32_000)


class ChatTokenEvent(_Resp):
    kind: Literal["token", "tool", "tool_result", "error", "done"]
    content: str | None = None
    data: dict[str, Any] | None = None


# ---- security center ----


class NetworkPolicyPatch(_Resp):
    allow_hosts: list[str] | None = None
    allow_http: bool | None = None
    max_response_bytes: int | None = None
    connect_timeout_seconds: float | None = None
    read_timeout_seconds: float | None = None


class FilesystemPolicyPatch(_Resp):
    allow_paths: list[str] | None = None
    deny_paths: list[str] | None = None
    max_read_bytes: int | None = None
    max_files_per_call: int | None = None


class SandboxPolicyPatch(_Resp):
    backend: SandboxBackend | None = None
    cpu_seconds: int | None = None
    memory_mb: int | None = None
    max_open_files: int | None = None
    max_processes: int | None = None
    timeout_seconds: int | None = None


class PluginAllowPatch(_Resp):
    allow: list[str] | None = None
    grants: list[str] | None = None


class PrivacyPatch(_Resp):
    privacy_mode: PrivacyMode | None = None
    raw_prompts: bool | None = None
    raw_model_outputs: bool | None = None


class GlobalPostureUpdate(_Resp):
    frozen: bool | None = None
    freeze_reason: str | None = None
    compliance_mode: Literal["standard", "audit"] | None = None
    allow_internal_ips: bool | None = None
    allow_raw_logging: bool | None = None
    default_privacy_mode: PrivacyMode | None = None
    confirm_agent_name: str | None = None
    reason: str | None = None


class InternalGrantCreate(_Resp):
    agent_name: str
    cidr: str
    reason: str
    ttl_hours: int = Field(default=4, ge=1, le=24)
    confirm_agent_name: str


class TrustedDocSourceCreate(_Resp):
    host: str
    notes: str = ""


# ---- skills ----


class PendingSkillView(_Resp):
    review_id: str
    agent_name: str
    namespace: str
    proposed_name: str
    proposed_description: str
    # ``api`` (default, discovery-flow) | ``behavior`` | ``knowledge``.
    # Lets the review UI filter and render distinctly per flavor.
    kind: str = "api"
    rationale: str = ""
    examples: list[str] = []
    success_criteria: str = ""
    service_name: str
    base_url: str
    auth_method: str
    required_hosts: list[str]
    required_secrets: list[str]
    confidence: float
    source_url: str
    discovered_at: OptUtcDatetime
    state: str


class SkillDecisionIn(_Resp):
    decision: Literal["approve", "reject"]
    notes: str | None = None
    final_name: str | None = None
    final_description: str | None = None


# ---- cost ----


class CostWindowResponse(_Resp):
    period: str
    total_usd: float
    by_provider: dict[str, float]
    by_agent: dict[str, float]
    by_model: dict[str, float]


class BudgetCreate(_Resp):
    budget_id: str
    scope: Literal["global", "agent", "provider"]
    scope_key: str
    period: Literal["daily", "weekly", "monthly"]
    limit_usd: float = Field(gt=0)
    soft_alert_usd: float = Field(default=0.0, ge=0.0)
    hard_stop: bool = True


# ---- audit ----


class AuditEntry(_Resp):
    ts: UtcDatetime
    actor: str
    kind: str
    target: str
    diff: str
    reason: str
    severity: str
