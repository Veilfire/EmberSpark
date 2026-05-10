"""Runtime engine — wires providers, memory, tool executor, and reflection together.

The plan → tool → loop pattern is implemented as a LangGraph
``StateGraph`` (spec §3, §7, §8). Each phase of an iteration is a
node; the loop is a conditional edge back to the planner. The same
helper methods that handled the legacy async loop are reused as node
bodies, so every existing side-effect (budget tick, span open,
forensic record, guardrail call, structured log) happens at the same
point and in the same order.

Public contract: every tool call still goes through ``ToolExecutor``,
every boundary still has a hard budget, every state transition still
emits an audit-friendly log line. The graph just makes the transitions
explicit and gives us LangGraph's checkpoint primitives if/when we
want them.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import structlog

from spark.config.enums import DataScope, TaskState
from spark.privacy.guardrails import apply_guardrails
from spark.config.models import Agent, Task
from spark.cost import CostTracker, record_usage
from spark.cost.tracker import BudgetExceeded as CostBudgetExceeded, check_budgets
from spark.errors import ErrorCode, SparkError
from spark.learning.playbooks import Playbook, PlaybookCandidate, PlaybookStore
from spark.learning.reflection_plus import (
    EnhancedReflectionInput,
    derive_playbook_candidate,
)
from spark.logging import EventType, get_logger
from spark.memory.embeddings import SentenceTransformersProvider
from spark.memory.long_term import LongTermMemory
from spark.memory.retrieval import retrieve
from spark.memory.task_memory import TaskMemory
from spark.persistence.db import session_scope
from spark.persistence.learning_repos import (
    AuditRepository,
    ModelCallEventRepository,
    PersonaRepository,
    PostureRepository,
    SkillRepository,
)
from spark.persistence.models import DeliverableRow, TaskRunRow
from spark.persistence.repositories import TaskRunRepository
from spark.plugins.base import BudgetExceeded, PermissionDenied
from spark.plugins.registry import PluginRegistry
from spark.plugins.tool_runtime import BudgetGuard, ToolExecutor
from spark.providers import build_chat_model
from spark.reflection.reflector import ReflectionOutcome, reflect
from spark.runtime.spans import reset_run_id, set_run_id, span
from spark.runtime.state import RunState
from spark.secrets import SecretManager
from spark.utils.ids import new_task_run_id
from spark.utils.time import isoformat, utcnow

log = get_logger("spark.engine")


@dataclass
class EngineResult:
    run_id: str
    state: TaskState
    summary: str
    result: Any
    iterations: int
    tool_calls: int
    model_calls: int
    reflection: ReflectionOutcome | None
    error: str | None = None


_TRIGGER_PAYLOAD_BUDGET_BYTES = 32_000


def _render_trigger_payload(payload: dict[str, Any]) -> str | None:
    """Render the trigger payload as a fenced JSON block for the planner.

    Caps the rendered size at ``_TRIGGER_PAYLOAD_BUDGET_BYTES``. Large
    inbound webhook bodies (GitHub PRs commonly exceed 100 KB) would
    otherwise consume the model's context budget. The full unabridged
    body is preserved on ``TaskRunRow.trigger_payload_json``.
    """
    try:
        serialized = json.dumps(payload, default=str, indent=2)
    except Exception:  # pragma: no cover — best-effort
        return None
    if len(serialized) > _TRIGGER_PAYLOAD_BUDGET_BYTES:
        truncated = serialized[: _TRIGGER_PAYLOAD_BUDGET_BYTES]
        marker = (
            f"\n... [truncated; full payload {len(serialized)} bytes — "
            "see run_id.trigger_payload_json for the unabridged copy]"
        )
        serialized = truncated + marker
    return f"```json\n{serialized}\n```"


def _make_tool_result_message(
    *,
    plugin_name: str,
    tool_call_id: str | None,
    body: str,
    is_error: bool,
) -> dict[str, Any]:
    """Format a tool result message for the next model turn.

    Two shapes:

    - **Native** (``tool_call_id`` set): emit a real ``tool``-role
      message with the id. LangChain's converter requires the id to
      pair the result with the original ``tool_calls`` request.
    - **Text-protocol** (``tool_call_id`` is ``None``): the planner
      asked for a tool via free-form JSON in its content, so there's
      no id. Use a plain ``user``-role message with a clear prefix —
      this round-trips through every provider including ones that
      reject orphan ``tool``-role messages.
    """
    label = f"Tool result for `{plugin_name}`" + (" (error)" if is_error else "")
    if tool_call_id is not None:
        return {
            "role": "tool",
            "name": plugin_name,
            "tool_call_id": tool_call_id,
            "content": body,
        }
    return {"role": "user", "content": f"{label}:\n{body}"}


async def _load_plugin_configs(
    plugin_names: list[str], registry: Any
) -> dict[str, dict[str, Any]]:
    """Load each plugin's operator-stored config so the system-prompt
    renderer can surface the effective allow_paths / allow_hosts / rules
    to the model.

    Failures are silent — a missing config row just means no constraint
    block for that plugin, which is fine; the schema-level defaults are
    already rendered as part of the args block.
    """
    from spark.plugins.config import load_plugin_config  # noqa: PLC0415

    configs: dict[str, dict[str, Any]] = {}
    for name in plugin_names:
        try:
            handle = registry.get(name)
        except KeyError:
            continue
        try:
            loaded = await load_plugin_config(name, handle.cls.config_schema)
            configs[name] = dict(loaded.defaults)
        except Exception as exc:  # pragma: no cover — best effort
            log.debug("plugin_config_load_failed", plugin=name, error=str(exc))
            continue
    return configs


def _try_bind_tools(
    model: Any, allowlist: list[str], registry: Any
) -> Any:
    """Bind every allowlisted plugin to the model as a native tool.

    Returns the bound model on success, the unmodified model when the
    provider doesn't expose ``bind_tools`` or rejects the spec. We
    leave the text-based tool protocol in place either way, so a fall-
    back to free-form ``{"tool": …}`` JSON in the response content
    still works.
    """
    if not allowlist:
        return model
    if not hasattr(model, "bind_tools"):
        log.info("engine.bind_tools_skipped", reason="no_bind_tools_method")
        return model
    try:
        from spark.runtime.tool_spec import build_native_tool_specs  # noqa: PLC0415

        specs = build_native_tool_specs(allowlist, registry)
        if not specs:
            return model
        bound = model.bind_tools(specs)
        log.info(
            "engine.bind_tools_ok",
            tool_count=len(specs),
            tools=[s["function"]["name"] for s in specs],
        )
        return bound
    except Exception as exc:  # pragma: no cover — provider quirks
        log.warning(
            "engine.bind_tools_failed",
            error=str(exc),
            error_class=type(exc).__name__,
        )
        return model


def _extract_openrouter_reported_cost(
    response_metadata: dict[str, Any], usage_metadata: dict[str, Any]
) -> float | None:
    """Pull OpenRouter's authoritative ``usage.cost`` out of a response.

    When the chat model is built with ``model_kwargs={"usage": {"include":
    True}}`` (see ``providers/factory.py``), OpenRouter's response includes
    a top-level ``usage.cost`` (USD float). langchain-openai's exact
    landing place varies across versions, so we check a few plausible
    locations and return the first numeric one we find.
    """
    candidates: list[Any] = []

    # langchain-openai >=0.2 sometimes lifts the OpenAI usage block under
    # response_metadata["token_usage"] or response_metadata["usage"].
    for key in ("token_usage", "usage"):
        block = response_metadata.get(key)
        if isinstance(block, dict):
            candidates.append(block.get("cost"))
            details = block.get("cost_details")
            if isinstance(details, dict):
                candidates.append(details.get("upstream_inference_cost"))
                candidates.append(details.get("total_cost"))

    # OpenRouter's enrichment-style "upstream" key.
    candidates.append(response_metadata.get("cost"))

    # Some langchain versions pass through extras as a nested dict on
    # usage_metadata under the "input_token_details" or "extras" keys.
    extras = usage_metadata.get("extras")
    if isinstance(extras, dict):
        candidates.append(extras.get("cost"))

    for c in candidates:
        if isinstance(c, (int, float)) and c > 0:
            return float(c)
    return None


def _coerce_result_text(value: Any) -> str | None:
    """Render a planner result (response.content) into UI-displayable text.

    Success runs leave a string or structured-blocks list in
    ``state.result``; failure runs leave ``{"error": "..."}``. We want
    a single text representation either way for the run-replay page.
    """
    if value is None:
        return None
    if isinstance(value, str):
        return value or None
    if isinstance(value, dict) and "error" in value and len(value) == 1:
        return f"_error_: {value['error']}"
    if isinstance(value, list):
        # Anthropic-style content blocks: [{"type": "text", "text": "..."}]
        parts: list[str] = []
        for block in value:
            if isinstance(block, dict):
                text = block.get("text")
                if isinstance(text, str):
                    parts.append(text)
        if parts:
            return "\n\n".join(parts)
    try:
        return json.dumps(value, default=str, indent=2)
    except Exception:  # pragma: no cover
        return str(value)


def _write_engine_deliverable(
    *, task_name: str, run_id: str, content: str
) -> dict[str, Any] | None:
    """Persist the planner's final response as a markdown file.

    Layout: ``<deliverables_root>/<task_name>/<run_id>.md``. Mirrors the
    existing image_gen plugin's path-validation guard against escaping
    the root via crafted task names.
    """
    from spark.config.runtime_config import get_data_volume

    dv = get_data_volume()
    if dv is None:
        return None
    deliverables_root = Path(dv.deliverables_path).expanduser().resolve()

    # Sanitize the task name: must be a single safe path segment.
    if (
        "/" in task_name
        or "\\" in task_name
        or task_name in {"", ".", ".."}
        or task_name.startswith(".")
    ):
        log.warning("deliverable.unsafe_task_name", task=task_name)
        return None

    target_dir = (deliverables_root / task_name).resolve()
    try:
        target_dir.relative_to(deliverables_root)
    except ValueError:
        log.warning("deliverable.escape_blocked", task=task_name)
        return None

    target_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    file_path = (target_dir / f"{run_id}.md").resolve()
    try:
        file_path.relative_to(deliverables_root)
    except ValueError:
        log.warning("deliverable.escape_blocked_file", task=task_name)
        return None

    file_path.write_text(content, encoding="utf-8")
    relative_path = str(file_path.relative_to(deliverables_root))
    return {
        "relative_path": relative_path,
        "size_bytes": file_path.stat().st_size,
        "absolute_path": str(file_path),
    }


def _format_run_error(exc: BaseException) -> str:
    """Format an exception for ``TaskRunRow.error``.

    SparkError → ``json.dumps(to_dict())`` so the replay endpoint can
    parse it back and the UI renders a FailureInspector. Anything else
    keeps the legacy ``"<ExcType>: <msg>"`` shape.
    """
    import json as _json  # noqa: PLC0415

    if isinstance(exc, SparkError):
        try:
            return _json.dumps(exc.to_dict(), default=str)
        except Exception:  # pragma: no cover — defensive
            return f"{type(exc).__name__}: {exc}"
    return f"{type(exc).__name__}: {exc}"


def _classify_tool_error(exc: BaseException) -> str:
    """Map an exception to a categorical error_class for the dashboard.

    Orders checks by specificity so subclass exceptions (e.g.
    ``PathDenied`` derives from ``PermissionError``) land in their tighter
    bucket.
    """
    from spark.plugins.base import BudgetExceeded as _BE, PermissionDenied as _PD
    from spark.sandbox.executor import (
        SandboxExecutionFailed,
        SandboxTimeout,
        SandboxUnavailable,
    )
    from spark.utils.net import UrlDenied
    from spark.utils.paths import PathDenied

    if isinstance(exc, PathDenied):
        return "path_denied"
    if isinstance(exc, UrlDenied):
        return "network_denied"
    if isinstance(exc, _BE):
        return "budget_exceeded"
    if isinstance(exc, _PD):
        return "permission_denied"
    if isinstance(exc, SandboxTimeout):
        return "sandbox_timeout"
    if isinstance(exc, SandboxUnavailable):
        return "sandbox_unavailable"
    if isinstance(exc, SandboxExecutionFailed):
        return "sandbox_denied"
    if isinstance(exc, TimeoutError):
        return "timeout"
    if isinstance(exc, PermissionError):
        return "permission_denied"
    return "plugin_raised"


def _extract_tool_call(
    message: Any,
) -> tuple[str, dict[str, Any], str | None] | None:
    """Pull the first tool-call from a LangChain-style AI message.

    Returns ``(plugin_name, args, tool_call_id_or_None)``:

    - Native path (provider supports tool-binding): the model emits
      ``message.tool_calls = [{name, args, id}]``. We use that directly
      and the ``id`` flows back into the tool-result message so the
      provider can correlate.
    - Text path (fallback for providers without bind_tools, or when
      bind_tools failed): parse a ``{"tool": …, "args": …}`` JSON
      object out of ``message.content``. No id exists, so we return
      ``None`` for it; the run-tool node emits the result as a plain
      ``user`` message instead.
    """
    tool_calls = getattr(message, "tool_calls", None) or []
    if tool_calls:
        first = tool_calls[0]
        name = first.get("name") or first.get("tool")
        args = first.get("args") or first.get("arguments") or {}
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                args = {}
        if name:
            tcid = first.get("id") or first.get("tool_call_id")
            return str(name), dict(args), str(tcid) if tcid else None

    content = getattr(message, "content", "") or ""
    if isinstance(content, str):
        text_call = _find_tool_call_in_text(content)
        if text_call is not None:
            return text_call[0], text_call[1], None
    return None


def _find_tool_call_in_text(text: str) -> tuple[str, dict[str, Any]] | None:
    """Extract a ``{"tool": ..., "args": ...}`` JSON object from
    free-form model text.

    Many providers (Anthropic-on-Bedrock, DDG-routed Anthropic via
    OpenRouter) ignore native tool-binding and emit conversational
    preamble *followed* by the JSON tool call we asked for in the
    system prompt:

        I'll fact-check this... starting with a search.

        {"tool": "web_search", "args": {"query": "..."}}

    The previous parser only handled the case where the content was
    *only* JSON (``content.strip().startswith("{")``). That misses
    every preamble-then-JSON response, dropping every tool call on
    the floor and producing 0 tool_calls runs.

    Scan for balanced ``{...}`` blocks, try the *last* one first
    (most likely the actual tool call), and return on first match
    that has a ``tool`` key.
    """
    candidates: list[str] = []
    depth = 0
    start = -1
    in_str = False
    escape = False
    for i, ch in enumerate(text):
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
            continue
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start != -1:
                    candidates.append(text[start : i + 1])
                    start = -1
    # Last-first: the tool call is usually at the END of the message.
    for cand in reversed(candidates):
        try:
            parsed = json.loads(cand)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict) and "tool" in parsed:
            return str(parsed["tool"]), dict(parsed.get("args", {}))
    return None


class RuntimeEngine:
    """Orchestrates a single task run."""

    def __init__(
        self,
        *,
        agent: Agent,
        task: Task,
        secrets: SecretManager,
        plugin_registry: PluginRegistry,
        chat_model: Any | None = None,
        trigger_payload: dict[str, Any] | None = None,
    ) -> None:
        self.agent = agent
        self.task = task
        self.secrets = secrets
        self.registry = plugin_registry
        self.chat_model = chat_model
        self._trigger_payload = trigger_payload

        rc = agent.spec.runtime
        self.budget = BudgetGuard(
            max_tool_calls=task.spec.budgets.max_tool_calls or rc.max_tool_calls,
            max_model_calls=task.spec.budgets.max_model_calls or rc.max_model_calls,
            max_iterations=rc.max_iterations,
            max_tokens_per_run=(
                task.spec.budgets.max_tokens_per_run or rc.max_tokens_per_run
            ),
        )
        self.tool_executor = ToolExecutor(
            registry=self.registry,
            secrets=self.secrets,
            agent_spec=agent.spec,
            budget=self.budget,
            agent_name=agent.metadata.name,
        )
        self._task_memory = TaskMemory()
        self._long_term: LongTermMemory | None = None
        if agent.spec.memory.long_term_memory and agent.spec.memory.long_term_memory.enabled:
            ltm = agent.spec.memory.long_term_memory
            # Data-volume resolution: if the runtime has an active data volume
            # AND the agent's LongTermMemoryConfig is still using its default
            # path (~/.spark/chroma), redirect to the data volume's
            # chroma_path so the store is persisted across container restarts
            # and carved out of any plugin's rw_paths.
            from spark.config.runtime_config import get_data_volume

            persist_path = ltm.persist_path
            dv = get_data_volume()
            if dv is not None:
                legacy_default = Path("~/.spark/chroma").expanduser()
                if Path(persist_path).expanduser() == legacy_default:
                    persist_path = dv.chroma_path
            self._long_term = LongTermMemory(
                namespace=ltm.namespace,
                collection_name=ltm.collection,
                persist_path=persist_path,
                embedder=SentenceTransformersProvider(ltm.embedder.model),
            )
        self._playbook_store = PlaybookStore()
        self._selected_playbook: Playbook | None = None
        self._cost_tracker: CostTracker | None = None
        self._start_monotonic: float = 0.0
        self._tool_sequence: list[str] = []
        self._saw_structured_error: bool = False
        self._forensic_writer: Any = None
        # Background OpenRouter enrichment tasks scheduled by
        # `_track_token_usage`. We don't await these on the run-finalize
        # path — they're fire-and-forget. Held here so the run holds a
        # reference (so they aren't GC'd before they run) and so a future
        # graceful-shutdown could choose to await them.
        self._enrichment_tasks: list[asyncio.Task[None]] = []

    async def run(self) -> EngineResult:
        run_id = new_task_run_id()
        state = RunState(
            run_id=run_id,
            task_name=self.task.metadata.name,
            agent_name=self.agent.metadata.name,
            objective=self.task.spec.objective,
            inputs={k: v for k, v in self.task.spec.inputs.items()},
            trigger_payload=self._trigger_payload,
        )

        # Freeze check + budget check before any work.
        await self._preflight()

        provider_cfg = self.agent.spec.runtime.provider
        self._cost_tracker = CostTracker(
            run_id=run_id,
            agent_name=state.agent_name,
            task_name=state.task_name,
            provider=provider_cfg.type,
            model=provider_cfg.model,
        )
        import time as _time
        self._start_monotonic = _time.monotonic()

        async with session_scope() as session:
            runs = TaskRunRepository(session)
            await runs.create(
                TaskRunRow(
                    run_id=run_id,
                    task_name=state.task_name,
                    agent_name=state.agent_name,
                    state="running",
                )
            )

        # Bind run_id into both the span context and structlog's contextvars
        # for correlation across every subsequent log event.
        run_token = set_run_id(run_id)
        structlog.contextvars.bind_contextvars(
            run_id=run_id, task=state.task_name, agent=state.agent_name
        )

        log.info(
            "task.started",
            event_type=EventType.TASK_STARTED,
            run_id=run_id,
            task=state.task_name,
            agent=state.agent_name,
        )

        # H2 — opt-in forensic capture.
        forensic_spec = getattr(self.task.spec, "forensic", None)
        if forensic_spec is not None and forensic_spec.enabled:
            try:
                from spark.forensic import ForensicWriter  # noqa: PLC0415

                self._forensic_writer = ForensicWriter(
                    run_id=run_id,
                    agent_name=state.agent_name,
                    task_name=state.task_name,
                    enabled_by="runtime",
                    enabled_reason=forensic_spec.reason,
                    ttl_hours=forensic_spec.ttl_hours,
                    secrets=self.secrets,
                )
                await self._forensic_writer.start()
            except Exception as exc:  # pragma: no cover
                log.warning("forensic.start_failed", error=str(exc), run_id=run_id)
                self._forensic_writer = None

        max_runtime = (
            self.task.spec.budgets.max_runtime_seconds
            or self.agent.spec.runtime.max_runtime_seconds
        )
        try:
            async with span(
                "run",
                task=state.task_name,
                agent=state.agent_name,
                max_runtime_seconds=max_runtime,
            ):
                result = await asyncio.wait_for(
                    self._run_loop(state), timeout=max_runtime
                )
            final_state = TaskState.COMPLETED
            error: str | None = None
        except asyncio.TimeoutError:
            result = {"error": f"task exceeded {max_runtime}s wall clock"}
            final_state = TaskState.FAILED
            error = "timeout"
            state.trace.append({"event": "timeout", "at": isoformat(utcnow())})
        except (BudgetExceeded, PermissionDenied) as exc:
            result = {"error": str(exc)}
            final_state = TaskState.FAILED
            # When the failure is a SparkError, persist the structured
            # to_dict() payload so the Run Replay UI can render a
            # FailureInspector. Plain prefix kept for legacy log filters.
            error = _format_run_error(exc)
            log.warning("task.budget_or_permission", event_type=EventType.BUDGET_EXCEEDED, error=str(exc))
        except Exception as exc:  # pragma: no cover — defensive
            result = {"error": str(exc)}
            final_state = TaskState.FAILED
            error = _format_run_error(exc)
            log.error("task.unhandled", error=str(exc))

        state.result = result
        state.status = final_state.value
        state.error = error

        # Record cost event (always — even on failure, tokens were spent).
        if self._cost_tracker is not None:
            try:
                await record_usage(self._cost_tracker)
            except Exception as exc:  # pragma: no cover
                log.warning("cost record failed", error=str(exc))

        # M1 — T1.4 + T4.3: credit memories that helped this run.
        try:
            from spark.memory.lifecycle import (  # noqa: PLC0415
                credit_successful_citations,
                penalize_unhelpful_citations,
            )

            retrieved_ids = [
                getattr(m, "memory_id", None) or m.get("memory_id")
                for m in state.retrieved_memories
                if m
            ]
            retrieved_ids = [m for m in retrieved_ids if m]
            if retrieved_ids:
                if final_state == TaskState.COMPLETED:
                    await credit_successful_citations(retrieved_ids)
                else:
                    await penalize_unhelpful_citations(retrieved_ids)
        except Exception as exc:  # pragma: no cover
            log.warning("memory_credit_failed", error=str(exc))

        # Compute duration once for learning updates.
        import time as _time
        duration_seconds = max(0.0, _time.monotonic() - self._start_monotonic)

        # Reflection — only on success and only if enabled.
        reflection: ReflectionOutcome | None = None
        if self.agent.spec.runtime.reflection and final_state == TaskState.COMPLETED:
            model = self._ensure_chat_model()
            try:
                reflection = await reflect(
                    model=model,
                    objective=state.objective,
                    trace=state.trace,
                    long_term=self._long_term,
                    agent_id=self.agent.metadata.name,
                    privacy_mode=self.agent.spec.runtime.privacy_mode,
                    task_id=state.run_id,
                )
            except Exception as exc:  # pragma: no cover
                log.warning("reflection failed", error=str(exc))

        # Update playbook bandit stats and optionally register a new playbook.
        try:
            await self._update_learning(
                state=state,
                success=final_state == TaskState.COMPLETED,
                reflection=reflection,
                duration_seconds=duration_seconds,
            )
        except Exception as exc:  # pragma: no cover
            log.warning("learning update failed", error=str(exc))

        # Coerce the raw state.result into a string for persistence + UI.
        # Success path: state.result is the planner's response.content
        # (str | None | structured blocks). Failure paths assign a
        # ``{"error": "..."}`` dict above. Either way, render to text.
        result_text = _coerce_result_text(state.result)

        # If the task spec opts in, write the result to the deliverables
        # volume. We do this BEFORE the DB commit so a failed write never
        # leaves a DeliverableRow pointing at a missing file.
        deliverable_meta = None
        if (
            final_state == TaskState.COMPLETED
            and result_text
            and self.task.spec.output.type == "file"
        ):
            try:
                deliverable_meta = _write_engine_deliverable(
                    task_name=state.task_name,
                    run_id=run_id,
                    content=result_text,
                )
            except Exception as exc:  # pragma: no cover — best-effort
                log.warning(
                    "deliverable.write_failed",
                    error=str(exc),
                    run_id=run_id,
                )

        # Persist finish state.
        async with session_scope() as session:
            runs = TaskRunRepository(session)
            await runs.finish(
                run_id,
                state=final_state.value,
                error=error,
                summary=reflection.record.summary if reflection else None,
                iterations=self.budget.iterations,
                model_calls=self.budget.model_calls,
                tool_calls=self.budget.tool_calls,
                result_text=result_text,
            )
            if deliverable_meta is not None:
                session.add(
                    DeliverableRow(
                        run_id=run_id,
                        task_name=state.task_name,
                        relative_path=deliverable_meta["relative_path"],
                        size_bytes=deliverable_meta["size_bytes"],
                        kind="markdown",
                        source="engine",
                    )
                )

        event = (
            EventType.TASK_COMPLETED
            if final_state == TaskState.COMPLETED
            else EventType.TASK_FAILED
        )
        log.info("task.finished", event_type=event, run_id=run_id, state=final_state.value)

        if self._forensic_writer is not None:
            try:
                await self._forensic_writer.finalize()
            except Exception as exc:  # pragma: no cover
                log.warning("forensic.finalize_failed", error=str(exc))

        self._task_memory.clear()
        reset_run_id(run_token)
        structlog.contextvars.unbind_contextvars("run_id", "task", "agent")

        return EngineResult(
            run_id=run_id,
            state=final_state,
            summary=reflection.record.summary if reflection else (error or "done"),
            result=result,
            iterations=self.budget.iterations,
            tool_calls=self.budget.tool_calls,
            model_calls=self.budget.model_calls,
            reflection=reflection,
            error=error,
        )

    async def _run_loop(self, state: RunState) -> Any:
        """Compile + drive the LangGraph state machine for this run.

        The graph is rebuilt per-run because each node closes over
        ``state`` and the per-run ``self.budget``. Compilation is
        cheap; LangGraph caches its own internal structures.
        """
        graph = self._compile_graph(state)
        # Initial state: empty messages list, no pending tool call.
        # ``prepare`` populates context + system/user messages before
        # the first ``invoke``.
        initial: dict[str, Any] = {
            "messages": [],
            "pending_tool_call": None,
            "result": None,
            "done": False,
        }
        # ``recursion_limit`` must accommodate the worst-case path
        # length. Each iteration walks ~5 nodes; bound it generously
        # against the iteration budget so LangGraph never short-circuits
        # before our own ``BudgetGuard`` does.
        config = {"recursion_limit": max(50, self.budget.max_iterations * 8)}
        final = await graph.ainvoke(initial, config=config)
        return final.get("result")

    # ------------------------------------------------------------------
    # LangGraph nodes — each is a thin wrapper around an existing helper.
    # State carried through the graph: ``messages``, ``pending_tool_call``,
    # ``result``, ``done``. The richer ``RunState`` lives on the engine
    # closure so we can mutate ``state.trace`` etc. in place — LangGraph
    # only needs the typed dict for routing decisions.
    # ------------------------------------------------------------------

    def _compile_graph(self, state: RunState) -> Any:
        """Build the per-run StateGraph. Imported lazily so a build that
        skips ``pip install langgraph`` still imports this module.
        """
        from langgraph.graph import END, StateGraph  # noqa: PLC0415

        graph: Any = StateGraph(dict)

        async def prepare(s: dict[str, Any]) -> dict[str, Any]:
            return await self._node_prepare(state, s)

        async def invoke(s: dict[str, Any]) -> dict[str, Any]:
            return await self._node_invoke(state, s)

        async def guardrail(s: dict[str, Any]) -> dict[str, Any]:
            return await self._node_guardrail(state, s)

        async def classify(s: dict[str, Any]) -> dict[str, Any]:
            return await self._node_classify(state, s)

        async def run_tool(s: dict[str, Any]) -> dict[str, Any]:
            return await self._node_run_tool(state, s)

        graph.add_node("prepare", prepare)
        graph.add_node("invoke", invoke)
        graph.add_node("guardrail", guardrail)
        graph.add_node("classify", classify)
        graph.add_node("run_tool", run_tool)
        graph.set_entry_point("prepare")
        graph.add_edge("prepare", "invoke")
        graph.add_edge("invoke", "guardrail")
        graph.add_edge("guardrail", "classify")

        def route_after_classify(s: dict[str, Any]) -> str:
            if s.get("done"):
                return END
            if s.get("pending_tool_call") is not None:
                return "run_tool"
            return END

        graph.add_conditional_edges(
            "classify",
            route_after_classify,
            {"run_tool": "run_tool", END: END},
        )
        graph.add_edge("run_tool", "invoke")
        return graph.compile()

    async def _node_prepare(
        self, state: RunState, s: dict[str, Any]
    ) -> dict[str, Any]:
        """Memory retrieval + initial system/user messages."""
        async with span("prepare_context"):
            await self._retrieve_memory_context(state)
        messages: list[dict[str, Any]] = [
            {
                "role": "system",
                "content": await self._system_prompt(state),
            },
            {
                "role": "user",
                "content": state.objective or "Execute the configured task.",
            },
        ]
        return {"messages": messages}

    async def _node_invoke(
        self, state: RunState, s: dict[str, Any]
    ) -> dict[str, Any]:
        """Tick budgets, hot-reload system prompt, record forensic
        prompt+model, invoke the model. Returns the response on the
        graph state for downstream nodes."""
        messages: list[dict[str, Any]] = list(s.get("messages") or [])

        self.budget.tick_iter()
        self.budget.tick_model()
        log.info(
            "budget.tick",
            event_type=EventType.BUDGET_TICK,
            kind="iter",
            current=self.budget.iterations,
            limit=self.budget.max_iterations,
        )
        log.info(
            "budget.tick",
            event_type=EventType.BUDGET_TICK,
            kind="model",
            current=self.budget.model_calls,
            limit=self.budget.max_model_calls,
        )

        # Hot-reload the system prompt on every iteration so persona
        # edits take effect on the very next model call without a
        # restart. Sub-ms overhead (one indexed DB read).
        messages[0] = {
            "role": "system",
            "content": await self._system_prompt(state),
        }

        # prompt.composed — counts only, never raw content.
        system_content = str(messages[0]["content"])
        user_content = str(messages[1]["content"]) if len(messages) > 1 else ""
        tool_total = sum(len(str(m.get("content", ""))) for m in messages[2:])
        mem_count = len(state.retrieved_memories)

        if self._forensic_writer is not None:
            try:
                await self._forensic_writer.record_prompt(
                    iteration=self.budget.iterations,
                    system_prompt=system_content,
                    user_message=user_content,
                    memory_context=[
                        {
                            "memory_id": getattr(m, "memory_id", None),
                            "summary": getattr(m, "summary", None),
                            "memory_type": getattr(m, "memory_type", None),
                            "score": getattr(m, "score", None),
                        }
                        for m in state.retrieved_memories
                    ],
                    playbook_id=(
                        self._selected_playbook.playbook_id
                        if self._selected_playbook is not None
                        else None
                    ),
                    message_count=len(messages),
                )
            except Exception as exc:  # pragma: no cover
                log.warning("forensic.record_prompt_failed", error=str(exc))
        log.info(
            "prompt.composed",
            event_type=EventType.PROMPT_COMPOSED,
            system_chars=len(system_content),
            user_chars=len(user_content),
            tool_history_chars=tool_total,
            memory_count=mem_count,
            playbook_id=(
                self._selected_playbook.playbook_id
                if self._selected_playbook is not None
                else None
            ),
            iteration=self.budget.iterations,
        )

        log.info(
            "model.invoke",
            event_type=EventType.MODEL_INVOKED,
            iteration=self.budget.iterations,
        )
        model = self._ensure_chat_model()
        async with span("plan", iteration=self.budget.iterations):
            response = await self._invoke_model(model, messages)
        state.trace.append(
            {
                "event": "model",
                "iteration": self.budget.iterations,
                "content_shape": type(getattr(response, "content", "")).__name__,
                "at": isoformat(utcnow()),
            }
        )

        if self._forensic_writer is not None:
            try:
                await self._forensic_writer.record_model(
                    iteration=self.budget.iterations,
                    provider=self.agent.spec.runtime.provider.type,
                    model=self.agent.spec.runtime.provider.model,
                    content=str(getattr(response, "content", "") or ""),
                    reasoning_blocks=list(
                        getattr(response, "reasoning_blocks", None) or []
                    ),
                    tool_calls_requested=[
                        dict(tc)
                        for tc in (getattr(response, "tool_calls", None) or [])
                    ],
                    stop_reason=getattr(response, "stop_reason", None),
                )
            except Exception as exc:  # pragma: no cover
                log.warning("forensic.record_model_failed", error=str(exc))

        return {"messages": messages, "response": response}

    async def _node_guardrail(
        self, state: RunState, s: dict[str, Any]
    ) -> dict[str, Any]:
        """Apply the data-class guardrail on the model output. Block
        propagates up to the run-level handler; redact rewrites the
        response content in place.

        IMPORTANT: every return MUST include ``response`` (and any other
        keys downstream nodes read). LangGraph 1.x with
        ``StateGraph(dict)`` treats a node's return value as the new
        partial-update — a bare ``{}`` return ends up wiping previously-
        set keys in some code paths, so ``response`` must be re-emitted
        explicitly even when this node makes no change. Without this
        the planner's response goes missing between ``invoke`` and
        ``classify`` and every run terminates with no tool call and no
        final answer.
        """
        response = s.get("response")
        raw_content = getattr(response, "content", None) if response else None
        if not isinstance(raw_content, str) or not raw_content:
            return {"response": response}
        try:
            outcome = await apply_guardrails(
                raw_content,
                agent_name=self.agent.metadata.name,
                scope=DataScope.MODEL_OUTPUT,
            )
            if outcome.text != raw_content:
                try:
                    object.__setattr__(response, "content", outcome.text)
                except Exception:
                    pass
        except SparkError as dc_exc:
            if dc_exc.code is ErrorCode.DATA_CLASS_BLOCKED:
                log.warning(
                    "runtime.data_class_blocked",
                    error_code=dc_exc.code.value,
                    detail=dc_exc.detail,
                    iteration=self.budget.iterations,
                )
                state.trace.append(
                    {
                        "event": "data_class_blocked",
                        "iteration": self.budget.iterations,
                        "detail": dc_exc.detail,
                        "at": isoformat(utcnow()),
                    }
                )
            raise
        return {"response": response}

    async def _node_classify(
        self, state: RunState, s: dict[str, Any]
    ) -> dict[str, Any]:
        """Branch the graph: tool-call request → ``run_tool``; otherwise
        terminate with the model's content as the run result."""
        response = s.get("response")
        tool_call = _extract_tool_call(response) if response is not None else None
        if tool_call is None:
            state.result = getattr(response, "content", None)
            return {"done": True, "result": state.result, "pending_tool_call": None}
        return {"pending_tool_call": tool_call}

    async def _node_run_tool(
        self, state: RunState, s: dict[str, Any]
    ) -> dict[str, Any]:
        """Execute the pending tool call and append the result (or
        structured error) to the message list."""
        messages: list[dict[str, Any]] = list(s.get("messages") or [])
        tool_call = s.get("pending_tool_call")
        if tool_call is None:
            return {"pending_tool_call": None}
        # ``tool_call_id`` is set when the call came back via the
        # provider's native ``tool_calls`` field (after a successful
        # bind_tools). It's None when we fell back to text-extraction.
        # The result-message format below branches on it.
        plugin_name, args, tool_call_id = tool_call

        try:
            async with span("tool_call", plugin=plugin_name):
                log.info(
                    "budget.tick",
                    event_type=EventType.BUDGET_TICK,
                    kind="tool",
                    current=self.budget.tool_calls + 1,
                    limit=self.budget.max_tool_calls,
                )
                outcome = await self.tool_executor.call(plugin_name, args)
        except BudgetExceeded:
            raise
        except Exception as exc:
            error_class = _classify_tool_error(exc)
            if isinstance(exc, SparkError):
                spark_err = exc
            else:
                spark_err = SparkError(
                    code=ErrorCode.PLUGIN_RAISED,
                    message=str(exc) or type(exc).__name__,
                    detail={
                        "plugin": plugin_name,
                        "exception_type": type(exc).__name__,
                    },
                )

            log.info(
                "tool.error_classified",
                event_type=EventType.TOOL_ERROR_CLASSIFIED,
                plugin=plugin_name,
                error_class=error_class,
                error_code=spark_err.code.value,
                detail=spark_err.detail,
                exception_type=type(exc).__name__,
            )
            # Fan out to the gate-failure notification family. The
            # helper is windowed per (agent, code, target) so a tight
            # loop hits the bell once. Best-effort — failures here
            # never escalate.
            try:
                from spark.errors.notify import notify_gate_failure  # noqa: PLC0415

                await notify_gate_failure(
                    spark_err,
                    agent_name=getattr(state, "agent_name", None),
                    run_id=getattr(state, "run_id", None),
                )
            except Exception:  # pragma: no cover — best-effort
                pass
            state.trace.append(
                {
                    "event": "tool_error",
                    "plugin": plugin_name,
                    "error_class": error_class,
                    "error_code": spark_err.code.value,
                    "error": spark_err.message,
                    "detail": spark_err.detail,
                    "remediation": spark_err.remediation,
                    "at": isoformat(utcnow()),
                }
            )

            payload: dict[str, Any] = {"error": spark_err.to_dict()}
            if not self._saw_structured_error:
                self._saw_structured_error = True
                messages.append(
                    {
                        "role": "system",
                        "content": (
                            "Tool errors use a stable `error.code` prefixed "
                            "`SPK_E_`. Branch on the code; the `detail` "
                            "object carries structured context; "
                            "`remediation` is a short hint. Examples: "
                            "SPK_E_PERMISSION_MISSING, SPK_E_URL_DENIED, "
                            "SPK_E_PATH_DENIED, SPK_E_METHOD_NOT_ALLOWED, "
                            "SPK_E_BUDGET_TOOL_EXCEEDED, "
                            "SPK_E_SECRET_NOT_FOUND, SPK_E_FROZEN."
                        ),
                    }
                )
            messages.append(
                _make_tool_result_message(
                    plugin_name=plugin_name,
                    tool_call_id=tool_call_id,
                    body=json.dumps(payload, default=str),
                    is_error=True,
                )
            )

            if self._forensic_writer is not None:
                try:
                    await self._forensic_writer.record_tool(
                        iteration=self.budget.iterations,
                        plugin=plugin_name,
                        args=args,
                        error_code=spark_err.code.value,
                        error_detail=spark_err.detail,
                    )
                except Exception as exc2:  # pragma: no cover
                    log.warning(
                        "forensic.record_tool_error_failed", error=str(exc2)
                    )
            return {"messages": messages, "pending_tool_call": None}

        self._tool_sequence.append(plugin_name)
        state.tool_outputs.append(
            {
                "plugin": plugin_name,
                "redactions": list(outcome.redactions),
                "result": outcome.filtered.content,
            }
        )

        if self._forensic_writer is not None:
            try:
                await self._forensic_writer.record_tool(
                    iteration=self.budget.iterations,
                    plugin=plugin_name,
                    args=args,
                    raw_result=outcome.raw_result,
                    filtered_result=outcome.filtered.content,
                    redactions=list(outcome.redactions),
                )
            except Exception as exc:  # pragma: no cover
                log.warning("forensic.record_tool_failed", error=str(exc))
        state.trace.append(
            {
                "event": "tool",
                "plugin": plugin_name,
                "redactions": list(outcome.redactions),
                "at": isoformat(utcnow()),
            }
        )
        messages.append(
            _make_tool_result_message(
                plugin_name=plugin_name,
                tool_call_id=tool_call_id,
                body=json.dumps(outcome.filtered.content, default=str),
                is_error=False,
            )
        )
        return {"messages": messages, "pending_tool_call": None}

    async def _retrieve_memory_context(self, state: RunState) -> None:
        # Playbook selection (learning layer B) — runs even if there's no long-term memory.
        try:
            self._selected_playbook = await self._playbook_store.select_for_run(
                agent_name=state.agent_name,
                objective=state.objective,
                available_tools=self.agent.spec.plugins.allow,
            )
            if self._selected_playbook is not None:
                log.info(
                    "playbook.selected",
                    playbook_id=self._selected_playbook.playbook_id,
                    name=self._selected_playbook.name,
                    success_rate=self._selected_playbook.success_rate,
                    uses=self._selected_playbook.uses,
                )
        except Exception as exc:  # pragma: no cover
            log.warning("playbook selection failed", error=str(exc))

        from spark.config.enums import Sensitivity as _Sens
        from spark.privacy.filtering import filter_for_model as _filter_for_model

        privacy_mode = self.agent.spec.runtime.privacy_mode

        # Approved skills — retrieved into context and re-filtered so a
        # reviewer-edited description can't slip secrets into the prompt.
        try:
            async with session_scope() as session:
                skill_repo = SkillRepository(session)
                approved = await skill_repo.list_for_agent(state.agent_name)
            for s in approved[:5]:
                gate = _filter_for_model(
                    {"name": s.name, "description": s.description},
                    privacy_mode=privacy_mode,
                    declared_sensitivity=_Sens.LOW,
                )
                safe = gate.content if isinstance(gate.content, dict) else {}
                state.retrieved_memories.append(
                    {
                        "memory_id": s.skill_id,
                        "summary": f"[SKILL] {safe.get('name', s.name)}: {safe.get('description', '')}",
                        "memory_type": "skill",
                        "source_type": "reflection",
                        "sensitivity": "low",
                        "confidence": s.confidence,
                        "score": 1.0,
                        "redactions": list(gate.redactions),
                    }
                )
        except Exception as exc:  # pragma: no cover
            log.warning("skill retrieval failed", error=str(exc))

        if self._long_term is None or not state.objective:
            return
        ltm_cfg = self.agent.spec.memory.long_term_memory
        assert ltm_cfg is not None
        try:
            hits = await retrieve(
                long_term=self._long_term,
                query=state.objective,
                privacy_mode=privacy_mode,
                top_k=ltm_cfg.retrieval.top_k,
                min_score=ltm_cfg.retrieval.min_score,
                recency_weight=ltm_cfg.retrieval.recency_weight,
                confidence_weight=ltm_cfg.retrieval.confidence_weight,
            )
        except Exception as exc:  # pragma: no cover
            log.warning("memory retrieval failed", error=str(exc))
            return

        filtered_hits: list[dict[str, object]] = []
        for m in hits:
            try:
                sensitivity = _Sens(m.sensitivity)
            except ValueError:
                continue
            gate = _filter_for_model(
                m.summary,
                privacy_mode=privacy_mode,
                declared_sensitivity=sensitivity,
            )
            if not isinstance(gate.content, str):
                continue
            row = m.__dict__.copy()
            row["summary"] = gate.content
            row["redactions"] = list(gate.redactions)
            filtered_hits.append(row)

        state.retrieved_memories.extend(filtered_hits)
        if filtered_hits:
            log.info(
                "memory.retrieved",
                event_type=EventType.MEMORY_RETRIEVED,
                count=len(filtered_hits),
                namespace=ltm_cfg.namespace,
            )

    async def _preflight(self) -> None:
        """Global posture + budget checks before any work starts."""
        async with session_scope() as session:
            posture = await PostureRepository(session).get()
            if posture.frozen:
                raise PermissionDenied(
                    f"Spark is frozen: {posture.freeze_reason or 'no reason given'}",
                    code=ErrorCode.FROZEN,
                    detail={"reason": posture.freeze_reason or None},
                )
        try:
            await check_budgets(
                agent_name=self.agent.metadata.name,
                provider=self.agent.spec.runtime.provider.type,
            )
        except CostBudgetExceeded as exc:
            async with session_scope() as session:
                await AuditRepository(session).append(
                    actor="runtime",
                    kind="budget.hard_stop",
                    target=self.agent.metadata.name,
                    reason=str(exc),
                    severity="critical",
                )
            raise BudgetExceeded(
                str(exc),
                code=ErrorCode.BUDGET_COST_HARD_STOP,
                detail={"agent": self.agent.metadata.name},
            ) from exc

    async def _update_learning(
        self,
        *,
        state: RunState,
        success: bool,
        reflection: ReflectionOutcome | None,
        duration_seconds: float,
    ) -> None:
        # Record outcome against the selected playbook (if any).
        if self._selected_playbook is not None:
            await self._playbook_store.record_outcome(
                playbook_id=self._selected_playbook.playbook_id,
                run_id=state.run_id,
                success=success,
                duration_seconds=duration_seconds,
                tool_calls=self.budget.tool_calls,
                model_calls=self.budget.model_calls,
            )

        if not success:
            return

        # If no playbook was selected but the run used tools, derive a new one.
        if self._selected_playbook is None and self._tool_sequence:
            inp = EnhancedReflectionInput(
                objective=state.objective,
                tool_sequence=list(self._tool_sequence),
                success=success,
                duration_seconds=duration_seconds,
                tool_calls=self.budget.tool_calls,
                model_calls=self.budget.model_calls,
                trace=state.trace,
            )
            summary = reflection.record.summary if reflection else state.objective
            candidate = derive_playbook_candidate(inp, record_summary=summary)
            if candidate is not None:
                new_pb = await self._playbook_store.upsert_from_candidate(
                    agent_name=state.agent_name,
                    candidate=candidate,
                )
                # Attribute this run's success to the newly created playbook.
                await self._playbook_store.record_outcome(
                    playbook_id=new_pb.playbook_id,
                    run_id=state.run_id,
                    success=True,
                    duration_seconds=duration_seconds,
                    tool_calls=self.budget.tool_calls,
                    model_calls=self.budget.model_calls,
                )

    async def _system_prompt(self, state: RunState) -> str:
        """Compose the full system prompt, reading the active persona each call.

        Persona hot-reload: the DB read happens every time this function is
        invoked. Editing the active persona in the UI takes effect on the next
        model call.
        """
        # Load the active persona (may be None on first boot before seed).
        async with session_scope() as session:
            persona = await PersonaRepository(session).get_active()

        pieces: list[str] = []
        if persona is not None and persona.system_prompt:
            pieces.append(persona.system_prompt.strip())
            if persona.tone:
                pieces.append(f"Tone: {persona.tone.strip()}")
        else:
            # Fallback when no persona is seeded yet — matches the previous
            # hardcoded prompt.
            pieces.append(f"You are the Spark agent {self.agent.metadata.name!r}.")
            if self.agent.spec.description:
                pieces.append(self.agent.spec.description)

        pieces.append("You operate under strict budgets and a plugin allowlist.")
        pieces.append(
            f"Allowed plugins: {', '.join(self.agent.spec.plugins.allow) or '(none)'}."
        )
        pieces.append(f"Privacy mode: {self.agent.spec.runtime.privacy_mode.value}.")

        # Tool spec — every allowlisted plugin's class description, args,
        # types, defaults, enums, and constraints. Without this the model
        # only sees plugin *names* and has to infer semantics from priors;
        # with it, the model gets the same information a human reviewer
        # has when reading the schema. Operator-stored config is loaded
        # for each plugin and surfaced as an "Operator config" block
        # under the args, so the model knows the *real* allow_paths /
        # allow_hosts / rules etc. — preventing the "I'll write to
        # ~/output.md" hallucination when only /data/spark-volume/
        # deliverables is allowlisted.
        if self.agent.spec.plugins.allow:
            from spark.runtime.tool_spec import render_tools_block  # noqa: PLC0415

            configs = await _load_plugin_configs(
                self.agent.spec.plugins.allow, self.registry
            )
            pieces.append(
                render_tools_block(
                    self.agent.spec.plugins.allow,
                    self.registry,
                    configs=configs,
                )
            )

        if self._selected_playbook is not None:
            pb = self._selected_playbook
            pieces.append(
                "---\nRECOMMENDED PLAYBOOK (selected by bandit):\n"
                f"- name: {pb.name}\n"
                f"- success_rate: {pb.success_rate:.2f} over {pb.uses} uses\n"
                f"- tool sequence: {', '.join(pb.tool_sequence) or '(none)'}\n"
                f"- description: {pb.description}\n"
                "Consider following this sequence unless new context argues otherwise."
            )
        if state.retrieved_memories:
            pieces.append("---\nRETRIEVED MEMORIES + SKILLS (summaries only):")
            for m in state.retrieved_memories:
                pieces.append(f"- [{m.get('memory_type','fact')}] {m.get('summary','')}")
            pieces.append("---")
        if state.trigger_payload is not None:
            payload_block = _render_trigger_payload(state.trigger_payload)
            if payload_block:
                pieces.append("---\nTRIGGER PAYLOAD (the request that fired this run):")
                pieces.append(payload_block)
                pieces.append("---")
        pieces.append(
            "Respond with a JSON tool call object `{\"tool\": \"name\", \"args\": {...}}` "
            "when you need to invoke a plugin, otherwise respond with the final answer."
        )
        return "\n".join(p for p in pieces if p)

    async def _invoke_model(self, model: Any, messages: list[dict[str, Any]]) -> Any:
        import time as _time
        from datetime import UTC, datetime as _dt

        started_wall = _dt.now(tz=UTC)
        started_mono = _time.monotonic()
        if hasattr(model, "ainvoke"):
            response = await model.ainvoke(messages)
        else:  # pragma: no cover — sync fallback
            response = model.invoke(messages)
        latency_ms = int((_time.monotonic() - started_mono) * 1000)
        finished_wall = _dt.now(tz=UTC)
        await self._track_token_usage(
            response,
            started_at=started_wall,
            finished_at=finished_wall,
            latency_ms=latency_ms,
        )
        return response

    async def _track_token_usage(
        self,
        response: Any,
        *,
        started_at: Any | None = None,
        finished_at: Any | None = None,
        latency_ms: int = 0,
    ) -> None:
        usage = getattr(response, "usage_metadata", None)
        if not isinstance(usage, dict):
            return
        prompt = int(usage.get("input_tokens", 0) or 0)
        completion = int(usage.get("output_tokens", 0) or 0)
        total = prompt + completion

        # Cache + reasoning details (LangChain's standardized sub-dicts).
        input_details = usage.get("input_token_details") or {}
        output_details = usage.get("output_token_details") or {}
        cached_in = int(input_details.get("cache_read", 0) or 0)
        cache_create = int(input_details.get("cache_creation", 0) or 0)
        reasoning = int(output_details.get("reasoning", 0) or 0)

        # response_metadata carries provider-specific scalars: request_id,
        # gen_id (OpenRouter), system_fingerprint (OpenAI), etc.
        response_metadata = getattr(response, "response_metadata", None) or {}
        request_id = None
        if isinstance(response_metadata, dict):
            # OpenRouter / OpenAI: top-level "id". Anthropic also surfaces
            # a top-level id; some providers nest it under model_response.
            request_id = response_metadata.get("id") or response_metadata.get("request_id")

        # Run-aggregate path (existing behavior — keeps Cost Dashboard math).
        if self._cost_tracker is not None and (prompt or completion):
            self._cost_tracker.add(prompt, completion)

        # Per-call event path — persist a row with the rich breakdown.
        if self._cost_tracker is not None and (prompt or completion):
            try:
                await self._record_model_call(
                    started_at=started_at,
                    finished_at=finished_at,
                    latency_ms=latency_ms,
                    response=response,
                )
            except Exception as exc:  # pragma: no cover — telemetry never blocks a run
                log.warning("model_call_event.record_failed", error=str(exc))

        # Token-budget enforcement (spec §8.3). Independent of cost
        # tracking so it works even when no pricing is configured.
        # Raises BudgetExceeded which the engine's outer loop already
        # treats as a clean run-failure path.
        if total > 0:
            self.budget.tick_tokens(total)

    async def _record_model_call(
        self,
        *,
        started_at: Any | None,
        finished_at: Any | None,
        latency_ms: int,
        response: Any,
    ) -> None:
        """Persist a ``model_call_events`` row + maybe schedule OR
        enrichment. Delegates to :mod:`spark.cost.per_call` so chat
        sessions can call the same code path.
        """
        from spark.cost.per_call import (  # noqa: PLC0415
            record_model_call,
            schedule_openrouter_enrichment,
        )

        if self._cost_tracker is None:
            return
        result = await record_model_call(
            run_id=self._cost_tracker.run_id,
            sequence=self.budget.iterations,
            provider=self._cost_tracker.provider,
            model=self._cost_tracker.model,
            response=response,
            started_at=started_at,
            finished_at=finished_at,
            latency_ms=latency_ms,
        )
        if result is None:
            return
        row_id, request_id = result
        if (
            self._cost_tracker.provider == "openrouter"
            and isinstance(request_id, str)
            and request_id.startswith("gen-")
            and row_id
        ):
            api_key_ref = getattr(
                self.agent.spec.runtime.provider, "api_key_ref", None
            )
            if not api_key_ref:
                return
            try:
                api_key_secret = self.secrets.get(api_key_ref)
            except Exception:
                return
            api_key = (
                api_key_secret.get_secret_value()
                if hasattr(api_key_secret, "get_secret_value")
                else str(api_key_secret)
            )
            schedule_openrouter_enrichment(
                row_id=row_id,
                request_id=request_id,
                api_key=api_key,
                tasks=self._enrichment_tasks,
            )

    def _ensure_chat_model(self) -> Any:
        if self.chat_model is not None:
            return self.chat_model
        raw = build_chat_model(
            self.agent.spec.runtime.provider, self.secrets
        )
        # Bind the agent's allowlisted plugins as native tools when the
        # provider supports it. This gives the model proper tool grammar
        # (the schemas it would otherwise have to infer from the system
        # prompt) and makes it emit structured ``tool_calls`` instead of
        # text JSON. The text protocol stays in the system prompt as a
        # fallback for providers that don't implement bind_tools — same
        # extractor handles both.
        bound = _try_bind_tools(raw, self.agent.spec.plugins.allow, self.registry)
        self.chat_model = bound
        return self.chat_model
