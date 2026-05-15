"""The single execution seam all tool calls pass through.

Responsibilities (in order):
 1. Check plugin is in the agent allowlist.
 2. Check plugin's required permissions ⊆ agent grants.
 3. Check budget.
 4. Validate args against the plugin input_schema (strict Pydantic).
 5. Resolve only the declared secrets via the secret manager.
 6. Build a SandboxPolicy from the agent permissions + plugin network need.
 7. Dispatch to the sandbox executor.
 8. Validate the returned result against output_schema.
 9. If `filter_output_before_model`, apply privacy filtering + sensitivity gate.
10. Emit structured log events and return the result.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from spark.config.models import AgentSpec
from spark.errors import ErrorCode, SparkError
from spark.logging import EventType, get_logger
from spark.plugins.base import BudgetExceeded, PermissionDenied
from spark.plugins.config import load_plugin_config, merge_config_and_args
from spark.plugins.registry import PluginRegistry
from spark.privacy.filtering import FilterOutcome, filter_for_model
from spark.privacy.guardrails import apply_guardrails
from spark.config.enums import DataScope
from spark.sandbox.executor import run_sandboxed
from spark.sandbox.ipc import RequestFrame
from spark.sandbox.policy import build_policy
from spark.secrets import SecretManager

log = get_logger("spark.tool_runtime")


@dataclass(frozen=True)
class _InProcessCtx:
    """Minimal ctx surface for plugins that opt out of the sandbox.

    Mirrors the shape ``spark.sandbox.worker`` builds for sandboxed
    plugins so the same ``execute`` body runs in both modes. Used only
    by plugins that set ``runs_in_sandbox = False`` — currently
    ``propose_skill`` (typed DB write to ``skill_reviews`` plus an
    audit + notification fan-out, all of which need the parent's DB
    session and notification service).
    """

    secrets: dict[str, str]
    plugin_config: dict[str, Any]
    scratch_path: str | None
    deliverables_path: str | None
    privacy_mode: str
    agent_name: str | None


@dataclass
class ToolCallOutcome:
    plugin: str
    raw_result: Any
    filtered: FilterOutcome
    redactions: tuple[str, ...]


class BudgetGuard:
    """Per-run counter, enforced by the engine.

    ``max_tokens_per_run`` is optional (``None`` = unbounded). When
    set, it caps the sum of input + output tokens consumed by the run
    across every model call. The engine's ``_track_token_usage`` hook
    feeds this counter via :meth:`tick_tokens` after each invocation.
    """

    def __init__(
        self,
        *,
        max_tool_calls: int,
        max_model_calls: int,
        max_iterations: int,
        max_tokens_per_run: int | None = None,
    ) -> None:
        self.max_tool_calls = max_tool_calls
        self.max_model_calls = max_model_calls
        self.max_iterations = max_iterations
        self.max_tokens_per_run = max_tokens_per_run
        self.tool_calls = 0
        self.model_calls = 0
        self.iterations = 0
        self.tokens_used = 0

    def tick_tool(self) -> None:
        self.tool_calls += 1
        if self.tool_calls > self.max_tool_calls:
            raise BudgetExceeded(
                f"tool_calls budget exceeded ({self.tool_calls}/{self.max_tool_calls})",
                code=ErrorCode.BUDGET_TOOL_EXCEEDED,
                detail={"used": self.tool_calls, "limit": self.max_tool_calls},
            )

    def tick_model(self) -> None:
        self.model_calls += 1
        if self.model_calls > self.max_model_calls:
            raise BudgetExceeded(
                f"model_calls budget exceeded ({self.model_calls}/{self.max_model_calls})",
                code=ErrorCode.BUDGET_MODEL_EXCEEDED,
                detail={"used": self.model_calls, "limit": self.max_model_calls},
            )

    def tick_iter(self) -> None:
        self.iterations += 1
        if self.iterations > self.max_iterations:
            raise BudgetExceeded(
                f"iterations budget exceeded ({self.iterations}/{self.max_iterations})",
                code=ErrorCode.BUDGET_ITER_EXCEEDED,
                detail={"used": self.iterations, "limit": self.max_iterations},
            )

    def tick_tokens(self, tokens: int) -> None:
        """Record token usage from a model call. Skips when no cap is set
        or when the provider returned a non-positive count."""
        if self.max_tokens_per_run is None or tokens <= 0:
            return
        self.tokens_used += tokens
        if self.tokens_used > self.max_tokens_per_run:
            raise BudgetExceeded(
                f"tokens budget exceeded ({self.tokens_used}/{self.max_tokens_per_run})",
                code=ErrorCode.BUDGET_TOKEN_EXCEEDED,
                detail={
                    "used": self.tokens_used,
                    "limit": self.max_tokens_per_run,
                },
            )


class ToolExecutor:
    def __init__(
        self,
        *,
        registry: PluginRegistry,
        secrets: SecretManager,
        agent_spec: AgentSpec,
        budget: BudgetGuard,
        agent_name: str | None = None,
    ) -> None:
        self.registry = registry
        self.secrets = secrets
        self.agent = agent_spec
        self.budget = budget
        # Optional; when set, data-class guardrails resolve per-agent
        # overrides and grants. Legacy callers without the name still
        # get global policy + defaults.
        self.agent_name = agent_name

    async def call(self, plugin_name: str, args: dict[str, Any]) -> ToolCallOutcome:
        # 1. allowlist
        if plugin_name not in self.agent.plugins.allow:
            log.warning(
                "plugin denied — not in allowlist",
                event_type=EventType.PERMISSION_DENIED,
                plugin=plugin_name,
                error_code=ErrorCode.PLUGIN_NOT_ALLOWED.value,
            )
            raise PermissionDenied(
                f"plugin {plugin_name!r} not in agent allowlist",
                code=ErrorCode.PLUGIN_NOT_ALLOWED,
                detail={"plugin": plugin_name},
            )

        handle = self.registry.get(plugin_name)
        plugin_cls = handle.cls

        # 2. permissions
        granted = set(self.agent.permissions.grants)
        missing = plugin_cls.required_permissions - granted
        if missing:
            missing_values = sorted(p.value for p in missing)
            log.warning(
                "plugin denied — missing permissions",
                event_type=EventType.PERMISSION_DENIED,
                plugin=plugin_name,
                missing=missing_values,
                error_code=ErrorCode.PERMISSION_MISSING.value,
            )
            raise PermissionDenied(
                f"plugin {plugin_name!r} requires permissions {missing_values}",
                code=ErrorCode.PERMISSION_MISSING,
                detail={"plugin": plugin_name, "missing": missing_values},
            )

        # 3. budget
        self.budget.tick_tool()

        # 3b. operator plugin config — load, seed if missing, merge with args.
        # Operator-configured fields OVERRIDE model args on overlapping fields.
        loaded = await load_plugin_config(plugin_name, plugin_cls.config_schema)
        input_fields = set(plugin_cls.input_schema.model_fields.keys())
        merged_args, plugin_config = merge_config_and_args(
            config=loaded.defaults,
            args=args,
            input_field_names=input_fields,
        )

        # 4. validate args
        try:
            validated = plugin_cls.input_schema.model_validate(merged_args)
        except ValidationError as exc:
            # Log the structured detail but do not re-expose it in the error
            # message. `exc.errors()` can include field names that match the
            # agent's declared secret refs — we keep that in operator logs
            # only.
            error_count = len(exc.errors())
            log.warning(
                "plugin.args_invalid",
                event_type=EventType.PERMISSION_DENIED,
                plugin=plugin_name,
                errors=exc.errors(),
                error_code=ErrorCode.INPUT_SCHEMA_INVALID.value,
            )
            raise SparkError(
                code=ErrorCode.INPUT_SCHEMA_INVALID,
                message=f"invalid args for {plugin_name!r} ({error_count} validation error(s))",
                detail={"plugin": plugin_name, "error_count": error_count},
            ) from exc

        # 5. resolve secrets the plugin needs.
        #
        # Two sources, both keyed by the *value of the secret name* (so
        # plugin code can do ``ctx.secrets.get(api_key_secret)`` where
        # ``api_key_secret`` is itself read from operator config):
        #
        #   a. ``plugin_cls.required_secrets`` — static contract for
        #      plugins where the secret name is fixed at code time
        #      (e.g. ``smtp_password`` baked into the plugin).
        #
        #   b. Operator-config fields whose key ends in ``_secret`` —
        #      this is the convention every operator-configurable
        #      plugin uses (``web_search.api_key_secret``,
        #      ``telegram_messenger.bot_token_secret``,
        #      ``webhook.signing_key_secret``,
        #      ``email_sender.username_secret`` /
        #      ``password_secret``). Without this auto-injection the
        #      plugin has no way to read its credential and reports
        #      ``SECRET_NOT_FOUND`` even though the value sits in the
        #      vault under the right name.
        #
        # Vault misses on (b) are skipped silently here so the plugin
        # raises its own clearer error at ``execute`` time.
        from spark.secrets import SecretNotFound  # noqa: PLC0415

        secret_values: dict[str, str] = {}
        for name in plugin_cls.required_secrets:
            secret_values[name] = self.secrets.get(name).get_secret_value()

        # Walk the config recursively for ``*_secret`` field references.
        # The flat case (``plugin.api_key_secret = "foo"``) is what every
        # original plugin uses; the recursive case (``cloud_drive.
        # providers[].auth.token_secret = "bar"``) lets plugins compose
        # nested config without rewiring this loop per plugin.
        def _walk(node: Any) -> None:
            if isinstance(node, dict):
                for k, v in node.items():
                    if (
                        isinstance(k, str)
                        and k.endswith("_secret")
                        and isinstance(v, str)
                        and v
                        and v not in secret_values
                    ):
                        try:
                            secret_values[v] = self.secrets.get(v).get_secret_value()
                        except SecretNotFound:
                            continue
                    else:
                        _walk(v)
            elif isinstance(node, list):
                for item in node:
                    _walk(item)

        _walk(plugin_config)

        if secret_values:
            log.info(
                "secret injected",
                event_type=EventType.SECRET_REQUESTED,
                plugin=plugin_name,
                names=sorted(secret_values.keys()),
            )

        # 6. build sandbox policy
        policy = build_policy(
            self.agent.permissions,
            allow_network=plugin_cls.needs_network,
        )

        # 7. dispatch
        log.info(
            "tool.invoke",
            event_type=EventType.TOOL_INVOKED,
            plugin=plugin_name,
            tool_calls=self.budget.tool_calls,
        )

        # Surface the data-volume paths in the request frame so the sandbox
        # worker can populate `ctx.scratch_path` / `ctx.deliverables_path`.
        # These are None when the data volume is disabled.
        from spark.config.runtime_config import get_data_volume

        _dv = get_data_volume()
        scratch_path = str(_dv.scratch_path) if _dv is not None else None
        deliverables_path = str(_dv.deliverables_path) if _dv is not None else None

        # 7a. In-process bypass for plugins that opt out of the sandbox.
        # Reserved for plugins whose only side effect is a typed write
        # to EmberSpark's own SQLite (e.g. ``propose_skill``) — they
        # need DB access that the sandbox's filesystem isolation
        # deliberately blocks, and they have zero external surface
        # (no network, no shell, no fs writes outside the runtime DB).
        # Set ``runs_in_sandbox = False`` on the plugin class to opt in;
        # default is True so every existing plugin keeps its sandbox.
        if not getattr(plugin_cls, "runs_in_sandbox", True):
            ctx = _InProcessCtx(
                secrets=secret_values,
                plugin_config=plugin_config,
                scratch_path=scratch_path,
                deliverables_path=deliverables_path,
                privacy_mode=self.agent.runtime.privacy_mode.value
                if hasattr(self.agent.runtime.privacy_mode, "value")
                else str(self.agent.runtime.privacy_mode),
                agent_name=self.agent_name,
            )
            try:
                raw_obj = await plugin_cls().execute(validated, ctx)
            except Exception:
                raise
            # Mirror the sandbox path's serialization: dump the result
            # to JSON-compatible dict so the rest of the flow doesn't
            # need to know whether we sandboxed or not.
            if hasattr(raw_obj, "model_dump"):
                raw = raw_obj.model_dump(mode="json")
            else:
                raw = raw_obj
        else:
            request = RequestFrame(
                plugin_module=handle.module,
                plugin_class=handle.class_name,
                args=validated.model_dump(mode="json"),
                secrets=secret_values,
                plugin_config=plugin_config,
                scratch_path=scratch_path,
                deliverables_path=deliverables_path,
                privacy_mode=self.agent.runtime.privacy_mode.value
                if hasattr(self.agent.runtime.privacy_mode, "value")
                else str(self.agent.runtime.privacy_mode),
            )
            response = await run_sandboxed(request, policy)
            raw = response.result

        # 8. validate output
        try:
            plugin_cls.output_schema.model_validate(raw)
        except ValidationError as exc:
            log.warning(
                "plugin.output_invalid",
                event_type=EventType.PERMISSION_DENIED,
                plugin=plugin_name,
                errors=exc.errors(),
                error_code=ErrorCode.OUTPUT_SCHEMA_INVALID.value,
            )
            raise SparkError(
                code=ErrorCode.OUTPUT_SCHEMA_INVALID,
                message=f"invalid output from {plugin_name!r} ({len(exc.errors())} validation error(s))",
                detail={"plugin": plugin_name, "error_count": len(exc.errors())},
            ) from exc

        # 9. filter for model exposure
        privacy_mode = self.agent.runtime.privacy_mode
        if plugin_cls.filter_output_before_model:
            filtered = filter_for_model(
                raw,
                privacy_mode=privacy_mode,
                declared_sensitivity=plugin_cls.sensitivity,
            )
        else:
            filtered = FilterOutcome(
                content=raw,
                sensitivity=plugin_cls.sensitivity,
                redactions=(),
                truncated=False,
            )

        # 9b. Data-class guardrails — per-scope + per-agent policy.
        # Apply after the legacy filter so a redaction by the older
        # pipeline still benefits from the class-aware layer. Raises
        # SparkError(DATA_CLASS_BLOCKED) on blocks; redacts in place
        # otherwise.
        if isinstance(filtered.content, str):
            outcome = await apply_guardrails(
                filtered.content,
                agent_name=self.agent_name,
                scope=DataScope.TOOL_OUTPUT,
            )
            if outcome.text is not filtered.content:
                # Replace content with the redacted text; keep the
                # FilterOutcome metadata intact so downstream
                # observability lines up with the old pipeline.
                filtered = FilterOutcome(
                    content=outcome.text,
                    sensitivity=filtered.sensitivity,
                    redactions=tuple(
                        list(filtered.redactions)
                        + [f"data_class:{cls.value}" for cls, _ in outcome.levels_applied]
                    ),
                    truncated=filtered.truncated,
                )

        log.info(
            "tool.result",
            event_type=EventType.TOOL_RESULT_RECEIVED,
            plugin=plugin_name,
            redactions=list(filtered.redactions),
            truncated=filtered.truncated,
            sensitivity=plugin_cls.sensitivity.value,
        )

        return ToolCallOutcome(
            plugin=plugin_name,
            raw_result=raw,
            filtered=filtered,
            redactions=filtered.redactions,
        )
