"""Pydantic v2 models for Spark YAML configuration.

Every model is `extra="forbid"` and `frozen=True`. Discriminated unions are used
for provider and plugin configs so YAML typos fail fast with a clear error.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

from spark.config.enums import (
    Permission,
    PrivacyMode,
    SandboxBackend,
    TaskMode,
)

API_VERSION = "spark.veilfire.dev/v1alpha1"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Metadata(_Strict):
    name: str = Field(min_length=1, max_length=128, pattern=r"^[a-z0-9][a-z0-9._-]*$")
    labels: dict[str, str] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Providers (discriminated union on `type`)
# ---------------------------------------------------------------------------


class _ProviderBase(_Strict):
    model: str
    temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    max_tokens: int | None = Field(default=None, gt=0, le=200_000)
    timeout_seconds: float = Field(default=60.0, gt=0, le=600)


class OpenAIProviderConfig(_ProviderBase):
    type: Literal["openai"] = "openai"
    api_key_ref: str = Field(description="Secret name resolved via secret manager")
    base_url: str | None = None
    organization: str | None = None


class AnthropicProviderConfig(_ProviderBase):
    type: Literal["anthropic"] = "anthropic"
    api_key_ref: str


class OpenRouterProviderConfig(_ProviderBase):
    type: Literal["openrouter"] = "openrouter"
    api_key_ref: str
    referer: str | None = None
    app_title: str | None = None


class OllamaProviderConfig(_ProviderBase):
    type: Literal["ollama"] = "ollama"
    base_url: str = "http://localhost:11434"
    api_key_ref: str | None = None


ProviderConfig = Annotated[
    Union[  # noqa: UP007 — discriminator wants explicit Union
        OpenAIProviderConfig,
        AnthropicProviderConfig,
        OpenRouterProviderConfig,
        OllamaProviderConfig,
    ],
    Field(discriminator="type"),
]


# ---------------------------------------------------------------------------
# Runtime / budgets / privacy
# ---------------------------------------------------------------------------


class RuntimeConfig(_Strict):
    provider: ProviderConfig
    max_iterations: int = Field(default=12, ge=1, le=200)
    max_model_calls: int = Field(default=30, ge=1, le=500)
    max_tool_calls: int = Field(default=25, ge=0, le=500)
    max_runtime_seconds: int = Field(default=900, ge=1, le=86_400)
    # Optional cap on total tokens (prompt + completion across all model
    # calls) consumed by a single run. ``None`` = unbounded — the cost
    # tracker still records usage but doesn't hard-stop. Spec §8.3.
    max_tokens_per_run: int | None = Field(default=None, ge=1, le=10_000_000)
    privacy_mode: PrivacyMode = PrivacyMode.STRICT
    reflection: bool = True


class BudgetOverrides(_Strict):
    max_runtime_seconds: int | None = Field(default=None, ge=1, le=86_400)
    max_model_calls: int | None = Field(default=None, ge=1, le=500)
    max_tool_calls: int | None = Field(default=None, ge=0, le=500)
    max_tokens_per_run: int | None = Field(default=None, ge=1, le=10_000_000)


# ---------------------------------------------------------------------------
# Memory
# ---------------------------------------------------------------------------


class EmbedderConfig(_Strict):
    provider: Literal["sentence_transformers"] = "sentence_transformers"
    model: str = "BAAI/bge-small-en-v1.5"


class RetrievalConfig(_Strict):
    top_k: int = Field(default=6, ge=1, le=50)
    min_score: float = Field(default=0.72, ge=0.0, le=1.0)
    recency_weight: float = Field(default=0.1, ge=0.0, le=1.0)
    confidence_weight: float = Field(default=0.1, ge=0.0, le=1.0)


class RetentionConfig(_Strict):
    default_class: str = "review"


class LongTermMemoryConfig(_Strict):
    enabled: bool = True
    namespace: str = Field(min_length=1, max_length=128)
    backend: Literal["chroma"] = "chroma"
    collection: str = Field(min_length=1, max_length=128)
    persist_path: Path = Path("~/.spark/chroma")
    embedder: EmbedderConfig = Field(default_factory=EmbedderConfig)
    retrieval: RetrievalConfig = Field(default_factory=RetrievalConfig)
    retention: RetentionConfig = Field(default_factory=RetentionConfig)


class SessionMemoryConfig(_Strict):
    enabled: bool = True
    max_entries: int = Field(default=200, ge=1, le=10_000)


class MemorySharingConfig(_Strict):
    """Cross-agent memory sharing policy.

    Private memories live in the agent's own namespace. When ``read_global``
    is True, the agent may also retrieve memories from the shared
    ``__global__`` namespace. When ``write_global`` is True, the agent (or
    the operator on its behalf) may promote its own memories to the shared
    pool. Every cross-scope read/write is audited at ``elevated`` severity.

    ``max_cross_scope_sensitivity`` caps which memories can cross out of
    the agent's private namespace — memories of higher sensitivity never
    reach the shared pool regardless of other flags.
    """

    read_global: bool = False
    write_global: bool = False
    max_cross_scope_sensitivity: Literal["low", "moderate", "high"] = "moderate"


class MemoryConfig(_Strict):
    task_memory: bool = True
    session_memory: SessionMemoryConfig = Field(default_factory=SessionMemoryConfig)
    long_term_memory: LongTermMemoryConfig | None = None
    sharing: MemorySharingConfig = Field(default_factory=MemorySharingConfig)


# ---------------------------------------------------------------------------
# Permissions
# ---------------------------------------------------------------------------


class FilesystemPermissions(_Strict):
    allow_paths: list[Path] = Field(default_factory=list)
    deny_paths: list[Path] = Field(default_factory=list)
    max_read_bytes: int = Field(default=5_000_000, ge=0)
    max_files_per_call: int = Field(default=256, ge=0, le=10_000)


class NetworkPermissions(_Strict):
    allow_hosts: list[str] = Field(default_factory=list)
    allow_http: bool = False
    max_response_bytes: int = Field(default=5_000_000, ge=0)
    connect_timeout_seconds: float = Field(default=5.0, gt=0, le=60)
    read_timeout_seconds: float = Field(default=15.0, gt=0, le=300)

    @field_validator("allow_hosts")
    @classmethod
    def _norm(cls, v: list[str]) -> list[str]:
        return [h.strip().lower() for h in v if h.strip()]


class ShellPermissions(_Strict):
    enabled: bool = False  # hard default: shell is off


class SandboxConfig(_Strict):
    enabled: bool = True
    backend: SandboxBackend = SandboxBackend.AUTO
    cpu_seconds: int = Field(default=30, ge=1, le=3600)
    memory_mb: int = Field(default=512, ge=16, le=16_384)
    max_open_files: int = Field(default=128, ge=4, le=65_536)
    max_processes: int = Field(default=8, ge=1, le=256)
    timeout_seconds: int = Field(default=60, ge=1, le=3600)


class Permissions(_Strict):
    filesystem: FilesystemPermissions = Field(default_factory=FilesystemPermissions)
    network: NetworkPermissions = Field(default_factory=NetworkPermissions)
    shell: ShellPermissions = Field(default_factory=ShellPermissions)
    sandbox: SandboxConfig = Field(default_factory=SandboxConfig)
    grants: list[Permission] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Plugins
# ---------------------------------------------------------------------------


class PluginAllowlist(_Strict):
    allow: list[str] = Field(default_factory=list)
    config: dict[str, dict[str, str]] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


class LoggingConfig(_Strict):
    level: Literal["debug", "info", "warning", "error"] = "info"
    raw_prompts: bool = False
    raw_model_outputs: bool = False
    local_path: Path = Path("~/.spark/logs")


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------


class AgentSpec(_Strict):
    description: str = ""
    runtime: RuntimeConfig
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    plugins: PluginAllowlist = Field(default_factory=PluginAllowlist)
    permissions: Permissions = Field(default_factory=Permissions)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)


class Agent(_Strict):
    apiVersion: Literal["spark.veilfire.dev/v1alpha1"] = API_VERSION  # noqa: N815
    kind: Literal["Agent"] = "Agent"
    metadata: Metadata
    spec: AgentSpec


# ---------------------------------------------------------------------------
# Task
# ---------------------------------------------------------------------------


class CronSchedule(_Strict):
    type: Literal["cron"] = "cron"
    expression: str
    timezone: str = "UTC"
    # Optional window. APScheduler honors both — fires nothing before
    # ``start_at``, removes the job after ``end_at``. Datetimes must be
    # tz-aware; the loader/API validates/normalizes to UTC.
    start_at: datetime | None = None
    end_at: datetime | None = None


class IntervalSchedule(_Strict):
    type: Literal["interval"] = "interval"
    seconds: int = Field(gt=0, le=86_400 * 7)
    timezone: str = "UTC"
    start_at: datetime | None = None
    end_at: datetime | None = None


ScheduleConfig = Annotated[
    Union[CronSchedule, IntervalSchedule],  # noqa: UP007
    Field(discriminator="type"),
]


class SessionConfig(_Strict):
    name: str | None = None
    continuity: Literal["none", "bounded", "full"] = "none"


class OutputConfig(_Strict):
    type: Literal["file", "stdout", "memory"] = "stdout"
    path: Path | None = None


class FileChangedEvent(_Strict):
    type: Literal["file_changed"] = "file_changed"
    path: Path
    recursive: bool = True
    debounce_seconds: int = Field(default=5, ge=0, le=3600)


class HttpNewRowEvent(_Strict):
    type: Literal["http_new_row"] = "http_new_row"
    url: str
    allow_hosts: list[str] = Field(default_factory=list)
    poll_seconds: int = Field(default=60, ge=5, le=86_400)
    key_path: str = "id"  # JSON path identifying the "primary key" for dedup


class TelegramCommand(_Strict):
    """One slash-command the bot exposes to users.

    Telegram clients show registered commands as an autocomplete menu
    when the user types ``/``. Each command has a description and a
    routing decision: either a built-in handler (status, runs, help,
    cancel, whoami) or a named task to fire.
    """

    command: str = Field(
        min_length=1,
        max_length=32,
        pattern=r"^[a-z][a-z0-9_]*$",
        description="The command name. ``/help`` is registered as ``help``.",
    )
    description: str = Field(min_length=1, max_length=256)
    action: Literal["builtin", "run_task", "chat"] = "builtin"
    task: str | None = Field(
        default=None,
        max_length=128,
        description=(
            "Required when action='run_task'. The task fires with the "
            "arguments after the command (and the inbound message) as "
            "trigger_payload."
        ),
    )


class TelegramChatBinding(_Strict):
    """Maps a Telegram chat (DM or group) to a Spark agent.

    The binding decides:

    - **Who can talk to the bot in this chat** — ``allow_user_ids``
      gates per-user. Empty list means "anyone the chat already has
      access to" (fine for DMs and trusted internal groups; risky on
      public groups).
    - **Which agent runs** — ``agent`` is the name of an agent
      registered in this Spark deploy.
    - **Mode** — ``conversational`` treats every non-command message as
      a one-shot fire on the bound agent with the message as trigger
      payload. ``command_only`` ignores non-command messages.
    """

    chat_id: int = Field(
        description="Telegram chat ID. DMs are positive; groups are negative."
    )
    # Empty string is reserved for the legacy upconvert path — at fire
    # time, that's resolved against the task's own agent. New configs
    # should always set it explicitly.
    agent: str = Field(default="", max_length=128)
    allow_user_ids: list[int] = Field(default_factory=list)
    mode: Literal["conversational", "command_only"] = "conversational"
    # Explicit allowlist of task names this binding's users can fire
    # via ``/run <task> [args]``. Empty list (the default) means /run
    # is **disabled** for this binding — safe by default. Populate it
    # when you want users to be able to ad-hoc kick off tasks. Custom
    # commands defined under ``commands:`` are unaffected by this list
    # because each one is already operator-bound to a specific task.
    allow_run_tasks: list[str] = Field(default_factory=list, max_length=64)
    # Whether ``/cancel <run_id>`` is enabled for this binding. Even
    # when enabled, cancel is restricted to runs whose ``agent_name``
    # matches this binding's ``agent`` so a chat bound to one agent
    # can't reach across and stop another agent's work.
    allow_cancel: bool = False


class TelegramBotEvent(_Strict):
    """Long-poll a Telegram bot and route inbound messages.

    Replaces the simpler ``telegram_message`` event source. Each
    inbound message is matched against the bindings list, authorized
    against ``allow_user_ids`` of the matched binding, and either:

    - Handled as a slash command (``/help``, ``/runs``, ``/run …``,
      ``/cancel``, ``/whoami``, or any operator-defined command).
    - Fired as a one-shot task on the bound agent with the message as
      ``trigger_payload`` (when the binding is ``conversational``).

    The bot replies through the ``telegram_messenger`` plugin — the
    plugin's ``allow_chat_ids`` should be a superset of the chat IDs
    used in ``bindings`` so the agent can answer back.
    """

    type: Literal["telegram_bot"] = "telegram_bot"
    bot_token_secret: str = Field(
        min_length=1,
        max_length=128,
        description="Name of the secret in the age vault holding the Telegram bot token.",
    )
    bindings: list[TelegramChatBinding] = Field(
        min_length=1,
        max_length=64,
        description="At least one chat→agent binding.",
    )
    commands: list[TelegramCommand] = Field(
        default_factory=list,
        max_length=100,
        description=(
            "Custom commands beyond the built-in /help, /runs, /run, "
            "/cancel, /whoami. Published to Telegram via setMyCommands "
            "at startup so users get autocomplete."
        ),
    )
    poll_seconds: int = Field(default=10, ge=2, le=300)
    long_poll_timeout: int = Field(default=25, ge=0, le=60)
    typing_indicator: bool = Field(
        default=True,
        description=(
            "Send a 'typing' chat-action while a task is running so the "
            "user sees the bot is working."
        ),
    )


# Legacy alias — older agent YAMLs use ``type: telegram_message`` with a
# flat ``allow_chat_ids`` list. We keep the old class as a compatibility
# shim that the bot runner upconverts into a TelegramBotEvent at load.
class TelegramMessageEvent(_Strict):
    type: Literal["telegram_message"] = "telegram_message"
    bot_token_secret: str = Field(min_length=1, max_length=128)
    allow_chat_ids: list[int] = Field(default_factory=list)
    poll_seconds: int = Field(default=10, ge=2, le=300)
    long_poll_timeout: int = Field(default=25, ge=0, le=60)


EventTrigger = Annotated[
    Union[
        FileChangedEvent,
        HttpNewRowEvent,
        TelegramBotEvent,
        TelegramMessageEvent,
    ],  # noqa: UP007
    Field(discriminator="type"),
]


class RetryPolicy(_Strict):
    max_attempts: int = Field(default=3, ge=1, le=20)
    backoff_seconds: float = Field(default=5.0, ge=0.0, le=3600)
    backoff_multiplier: float = Field(default=2.0, ge=1.0, le=10.0)
    jitter_seconds: float = Field(default=2.0, ge=0.0, le=60)


class ApprovalConfig(_Strict):
    required: bool = False
    note: str = ""


class ForensicSpec(_Strict):
    """Opt-in per-task forensic capture (H2).

    When enabled, every iteration of the run writes an encrypted
    snapshot of the prompt → model → tool → memory chain into the
    ``forensic_captures`` + ``forensic_snapshots`` tables.

    ``reason`` is required when ``enabled=True`` so future readers can
    see *why* the run was captured.
    """

    enabled: bool = False
    ttl_hours: int = Field(default=24 * 7, ge=1, le=24 * 90)
    reason: str = Field(default="", max_length=500)

    @model_validator(mode="after")
    def _reason_required(self) -> ForensicSpec:
        if self.enabled and not self.reason.strip():
            raise ValueError("forensic.enabled requires a non-empty reason")
        return self


class TaskSpec(_Strict):
    agent: str
    mode: TaskMode
    schedule: ScheduleConfig | None = None
    objective: str = ""
    inputs: dict[str, str | int | float | bool] = Field(default_factory=dict)
    session: SessionConfig = Field(default_factory=SessionConfig)
    output: OutputConfig = Field(default_factory=OutputConfig)
    budgets: BudgetOverrides = Field(default_factory=BudgetOverrides)
    # F5 additions — optional, backward compatible.
    on: EventTrigger | None = None
    on_success: str | None = Field(default=None, description="Task to fire on success")
    on_failure: str | None = Field(default=None, description="Task to fire on failure")
    retry: RetryPolicy = Field(default_factory=RetryPolicy)
    approval: ApprovalConfig = Field(default_factory=ApprovalConfig)
    only_between: str | None = Field(
        default=None,
        description='Run-window constraint like "22:00-06:00 America/Vancouver"',
    )
    heartbeat_seconds: int | None = Field(default=None, ge=5, le=86_400)
    # H2 — opt-in forensic capture.
    forensic: ForensicSpec = Field(default_factory=ForensicSpec)

    @model_validator(mode="after")
    def _mode_schedule_constraints(self) -> TaskSpec:
        """Enforce mode-aware schedule rules so the task creator UI and
        the YAML loader agree on what's valid.

        Constraints:

        - ``one_shot`` — schedule optional. If present, ``start_at`` is
          allowed (delayed run) but ``end_at`` is not (one-shot has no
          recurrence to bound).
        - ``recurring`` — schedule required, both ``start_at`` and
          ``end_at`` required, and ``start_at`` must precede ``end_at``.
        - ``perpetual`` — schedule required, ``start_at`` required (we
          want the kickoff explicit), ``end_at`` must be null (perpetual
          by definition has no end).
        - ``event`` — schedule must be null (event tasks fire from
          external triggers, not the scheduler).
        """
        s = self.schedule
        m = self.mode

        if m is TaskMode.ONE_SHOT:
            if s is not None and s.end_at is not None:
                raise ValueError(
                    "one_shot tasks cannot have schedule.end_at "
                    "(one-shot has no recurrence to bound)"
                )
        elif m is TaskMode.RECURRING:
            if s is None:
                raise ValueError("recurring tasks require a schedule")
            if s.start_at is None or s.end_at is None:
                raise ValueError(
                    "recurring tasks require both schedule.start_at and "
                    "schedule.end_at — use mode=perpetual for an unbounded run"
                )
            if s.start_at >= s.end_at:
                raise ValueError("schedule.start_at must precede schedule.end_at")
        elif m is TaskMode.PERPETUAL:
            if s is None:
                raise ValueError("perpetual tasks require a schedule")
            if s.start_at is None:
                raise ValueError("perpetual tasks require schedule.start_at")
            if s.end_at is not None:
                raise ValueError(
                    "perpetual tasks cannot have schedule.end_at — "
                    "use mode=recurring for a finite window"
                )
        elif m is TaskMode.EVENT:
            if s is not None:
                raise ValueError(
                    "event tasks fire from external triggers — "
                    "remove the schedule block"
                )
        return self


class Task(_Strict):
    apiVersion: Literal["spark.veilfire.dev/v1alpha1"] = API_VERSION  # noqa: N815
    kind: Literal["Task"] = "Task"
    metadata: Metadata
    spec: TaskSpec


# ---------------------------------------------------------------------------
# Holder for secret refs during runtime
# ---------------------------------------------------------------------------


class ResolvedSecret(BaseModel):
    """A secret resolved at runtime; always SecretStr."""

    name: str
    value: SecretStr

    model_config = ConfigDict(frozen=True)
