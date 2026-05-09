"""SQLModel tables for the operational persistence layer.

The vector content for long-term memory lives in Chroma; here we keep only the
index row (id, namespace, metadata) so operators can query and prune.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)


class AgentRow(SQLModel, table=True):
    __tablename__ = "agents"

    name: str = Field(primary_key=True, max_length=128)
    description: str = ""
    config_hash: str = Field(max_length=64)
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class TaskRow(SQLModel, table=True):
    __tablename__ = "tasks"

    name: str = Field(primary_key=True, max_length=128)
    agent_name: str = Field(index=True, max_length=128)
    mode: str = Field(max_length=32)
    config_hash: str = Field(max_length=64)
    config_path: str | None = Field(default=None, max_length=1024)
    state: str = Field(default="created", max_length=32)
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class TaskRunRow(SQLModel, table=True):
    __tablename__ = "task_runs"

    run_id: str = Field(primary_key=True, max_length=64)
    task_name: str = Field(index=True, max_length=128)
    agent_name: str = Field(index=True, max_length=128)
    state: str = Field(default="running", max_length=32)
    started_at: datetime = Field(default_factory=_utcnow)
    finished_at: datetime | None = None
    error: str | None = None
    iterations: int = 0
    model_calls: int = 0
    tool_calls: int = 0
    summary: str | None = None
    # Model's last assistant message (the planner's "final answer" when it
    # stopped calling tools). Distinct from `summary`, which is the
    # reflector's compact post-run digest.
    result_text: str | None = None
    # Full inbound webhook / external trigger payload, JSON-serialized.
    # Populated when a run was kicked off with a payload (e.g. GitHub PR
    # webhook). The first 32 KB are also rendered into the planner's
    # system prompt; this column carries the unabridged copy.
    trigger_payload_json: str | None = None
    consecutive_failures: int = 0
    # Lineage: a pipe-delimited chain like
    # ``webhook:gh-pr|task:fetch|task:summarize`` recording how this run
    # was reached. Cycle / depth checks read this. Capped at 256 chars
    # because chains > depth-5 are already refused.
    triggered_by: str | None = Field(default=None, max_length=256)


class DeliverableRow(SQLModel, table=True):
    """A file artifact produced by a run.

    Replaces the previous filesystem-walk discovery in
    ``spark/web/api/deliverables.py`` with a DB-backed list. ``source``
    distinguishes engine-written outputs (``engine``) from
    plugin-written ones (``plugin``) and externally-dropped files
    (``external``); the watcher uses this to avoid double-firing
    DOWNLOAD_READY notifications for engine writes.
    """

    __tablename__ = "deliverables"

    id: int | None = Field(default=None, primary_key=True)
    run_id: str | None = Field(default=None, index=True, max_length=64)
    task_name: str | None = Field(default=None, index=True, max_length=128)
    relative_path: str = Field(index=True, max_length=1024)
    size_bytes: int = 0
    kind: str = Field(default="file", max_length=32)  # file | markdown | json | image | other
    source: str = Field(default="engine", max_length=32)  # engine | plugin | external
    created_at: datetime = Field(default_factory=_utcnow)


class ScheduleRow(SQLModel, table=True):
    __tablename__ = "schedules"

    task_name: str = Field(primary_key=True, max_length=128)
    trigger_type: str = Field(max_length=32)
    trigger_expression: str = Field(max_length=256)
    timezone: str = Field(default="UTC", max_length=64)
    next_run_at: datetime | None = None
    last_run_at: datetime | None = None
    enabled: bool = True


class SessionRow(SQLModel, table=True):
    __tablename__ = "sessions"

    session_id: str = Field(primary_key=True, max_length=64)
    name: str = Field(index=True, max_length=128)
    agent_name: str = Field(index=True, max_length=128)
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class SessionMemoryRow(SQLModel, table=True):
    __tablename__ = "session_memory"

    id: int | None = Field(default=None, primary_key=True)
    session_id: str = Field(index=True, max_length=64)
    kind: str = Field(max_length=32)  # summary | decision | marker
    content: str
    created_at: datetime = Field(default_factory=_utcnow)


class ChatTurnRow(SQLModel, table=True):
    """One assistant turn inside a chat session.

    The row exists so chat generation can be decoupled from the WebSocket
    lifecycle. The background task creating the turn owns this row and
    flushes ``assistant_message`` periodically; viewers (the UI tab or
    another one reopened after navigation) read it to replay the current
    partial state before subscribing to the live event broker.

    ``state`` transitions: ``running`` → ``completed`` | ``error`` |
    ``cancelled``. ``cancelled`` is set on process startup for any row
    left ``running`` — the task was lost with the previous server.
    """

    __tablename__ = "chat_turns"

    turn_id: str = Field(primary_key=True, max_length=64)
    session_id: str = Field(index=True, max_length=128)
    agent_name: str = Field(max_length=128)
    state: str = Field(default="running", max_length=16, index=True)
    user_message: str
    assistant_message: str = ""
    citations_json: str | None = None
    error: str | None = None
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)
    finished_at: datetime | None = None


class ReflectionRow(SQLModel, table=True):
    __tablename__ = "reflections"

    id: int | None = Field(default=None, primary_key=True)
    run_id: str = Field(index=True, max_length=64)
    success: bool
    summary: str
    failures: str | None = None
    lessons: str | None = None
    follow_ups: str | None = None
    created_at: datetime = Field(default_factory=_utcnow)


class LongTermMemoryIndexRow(SQLModel, table=True):
    __tablename__ = "long_term_memory_index"

    memory_id: str = Field(primary_key=True, max_length=64)
    agent_name: str = Field(index=True, max_length=128)
    namespace: str = Field(index=True, max_length=128)
    collection: str = Field(max_length=128)
    memory_type: str = Field(max_length=32)
    source_type: str = Field(max_length=32)
    sensitivity: str = Field(max_length=32)
    retention_class: str = Field(max_length=32)
    confidence: float = 0.5
    content_summary: str
    canonical_hash: str = Field(max_length=64, index=True)
    tags: str | None = None
    task_id: str | None = None
    session_id: str | None = None
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)
    # M1 enhancements — every column defaults to a benign value so
    # existing rows stay valid without a migration.
    usage_count: int = 0
    successful_citation_count: int = 0
    last_cited_at: datetime | None = None
    contradicts_with: str | None = None  # comma-separated memory_ids
    superseded_by: str | None = Field(default=None, max_length=64)
    provenance_json: str | None = None
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    alpha: float = 1.0  # Bayesian Beta posterior
    beta: float = 1.0
    status: str = Field(default="active", max_length=32)  # active | pending_review | quarantined | superseded
    circle_id: str | None = Field(default=None, max_length=64)  # null=private, "__global__"=global, else=circle
    is_anti_pattern: bool = False
    consensus_sources: str | None = None  # comma-separated agent names when consensus-promoted


class EntityMemoryRow(SQLModel, table=True):
    """Lightweight triple store for structured entity memory (T3.1)."""

    __tablename__ = "entity_memory"

    id: int | None = Field(default=None, primary_key=True)
    namespace: str = Field(index=True, max_length=128)
    subject: str = Field(index=True, max_length=256)
    predicate: str = Field(max_length=128)
    object: str = Field(max_length=1024)
    confidence: float = 0.5
    source_memory_id: str | None = Field(default=None, max_length=64)
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class MemoryCircleRow(SQLModel, table=True):
    """A named group of agents that can share a memory pool (T3.3)."""

    __tablename__ = "memory_circles"

    circle_id: str = Field(primary_key=True, max_length=64)
    name: str = Field(max_length=128)
    description: str = ""
    created_at: datetime = Field(default_factory=_utcnow)


class CircleMembershipRow(SQLModel, table=True):
    """Per-agent membership in a memory circle."""

    __tablename__ = "circle_memberships"

    id: int | None = Field(default=None, primary_key=True)
    circle_id: str = Field(index=True, max_length=64)
    agent_name: str = Field(index=True, max_length=128)
    can_read: bool = True
    can_write: bool = False
    created_at: datetime = Field(default_factory=_utcnow)


class PluginRegistryRow(SQLModel, table=True):
    __tablename__ = "plugin_registry"

    name: str = Field(primary_key=True, max_length=128)
    version: str = Field(max_length=64)
    module_hash: str = Field(max_length=64)
    first_seen_at: datetime = Field(default_factory=_utcnow)
    last_seen_at: datetime = Field(default_factory=_utcnow)


class SecretMetadataRow(SQLModel, table=True):
    __tablename__ = "secrets_metadata"

    name: str = Field(primary_key=True, max_length=128)
    backend: str = Field(max_length=32)
    description: str | None = None
    created_at: datetime = Field(default_factory=_utcnow)
    last_accessed_at: datetime | None = None
