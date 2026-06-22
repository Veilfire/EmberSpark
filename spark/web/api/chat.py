"""Chat routes — conversational task mode with detached sessions.

The WebSocket is intentionally decoupled from the model generation
task. Sending a message spawns a background task that writes its
partial output to ``chat_turns`` and publishes tokens to an in-process
broker. Any connected viewer (the tab that sent the message, another
tab, or the same tab after a navigation-and-return) subscribes to the
broker and replays the partial state from the DB first, then streams
live events.
"""

from __future__ import annotations

import asyncio
import json
import re
import secrets
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy import delete, select

from spark.config.enums import DataScope
from spark.errors.codes import ErrorCode, SparkError
from spark.logging import get_logger
from spark.memory.session_memory import SessionEntry, SessionMemory
from spark.persistence.db import session_scope
from spark.persistence.models import (
    AgentRow,
    ChatTurnRow,
    SessionMemoryRow,
    SessionRow,
)
from spark.privacy.guardrails import apply_guardrails
from spark.runtime.engine import (
    _classify_tool_error,
    _extract_tool_call,
    _load_plugin_configs,
    _make_tool_result_message,
)
from spark.utils.time import utcnow
from spark.web.api.chat_broker import (
    BrokerEvent,
    broker_for_session,
    create_broker,
    discard_broker,
    get_broker,
)
from spark.web.auth import Principal, get_auth, require_operator, require_viewer

log = get_logger("spark.chat")
# Flush accumulated assistant output to DB at most this often while a
# turn is streaming. Keeps the picture current for reconnecting viewers
# without generating a per-token write volley.
_DB_FLUSH_MIN_INTERVAL_SEC = 0.25

router = APIRouter()

_ID_PATTERN = re.compile(r"^[a-zA-Z0-9._-]{1,128}$")
MAX_CHAT_TURN_CHARS = 32_000
MAX_HISTORY_LIMIT = 200


class CreateSessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_name: str = Field(min_length=1, max_length=128)
    name: str = Field(default="chat", min_length=1, max_length=128)
    session_id: str | None = Field(default=None, max_length=128)

    @field_validator("agent_name", "name")
    @classmethod
    def _slug(cls, v: str) -> str:
        if not _ID_PATTERN.match(v):
            raise ValueError("must match ^[a-zA-Z0-9._-]{1,128}$")
        return v

    @field_validator("session_id")
    @classmethod
    def _slug_opt(cls, v: str | None) -> str | None:
        if v is None:
            return None
        if not _ID_PATTERN.match(v):
            raise ValueError("invalid session_id")
        return v


class UpdateSessionRequest(BaseModel):
    """Rename and/or pin a chat. Both fields optional; at least one required.

    Rename writes the free-text ``title`` (NOT the slug ``name``) — the same
    field the auto-titler populates — so a user rename is automatically
    protected from being relabelled on the first turn.
    """

    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=160)
    pinned: bool | None = None


class ChatContextConfig(BaseModel):
    """Per-turn context knobs sent by the UI."""

    model_config = ConfigDict(extra="forbid")

    max_history_messages: int = Field(default=20, ge=0, le=200)
    include_long_term_memory: bool = True
    ltm_top_k: int = Field(default=6, ge=0, le=50)
    ltm_min_score: float = Field(default=0.72, ge=0.0, le=1.0)
    include_global: bool = False
    pin_memory_ids: list[str] = Field(default_factory=list, max_length=20)
    exclude_memory_ids: list[str] = Field(default_factory=list, max_length=50)
    emit_citations: bool = True


class ChatTurnRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str = Field(min_length=1, max_length=MAX_CHAT_TURN_CHARS)
    agent_name: str = Field(min_length=1, max_length=128)
    context: ChatContextConfig | None = None

    @field_validator("agent_name")
    @classmethod
    def _slug(cls, v: str) -> str:
        if not _ID_PATTERN.match(v):
            raise ValueError("invalid agent_name")
        return v


@router.get("/sessions")
async def list_sessions(_: Principal = Depends(require_viewer)) -> list[dict[str, Any]]:
    async with session_scope() as session:
        result = await session.execute(
            select(SessionRow)
            .order_by(SessionRow.pinned.desc(), SessionRow.updated_at.desc())
            .limit(200)
        )
        rows = list(result.scalars().all())
    return [_session_view(r) for r in rows]


def _session_view(r: SessionRow) -> dict[str, Any]:
    """Serialize a chat row for the sidebar.

    ``title`` is the auto-generated 5-word label (or a user rename); it is
    null until the first turn finishes, in which case the UI falls back to
    ``session_id``.
    """
    return {
        "session_id": r.session_id,
        "name": r.name,
        "title": r.title,
        "pinned": r.pinned,
        "agent_name": r.agent_name,
        "created_at": r.created_at,
        "updated_at": r.updated_at,
    }


def _publish_session_event(kind: str, **fields: Any) -> None:
    """Best-effort SSE fan-out so open Chat tabs live-update. Never raises."""
    try:
        from spark.web.events import get_bus  # noqa: PLC0415

        get_bus().publish(kind, **fields)
    except Exception:  # pragma: no cover — bus is best-effort
        pass


@router.post("/sessions")
async def create_session(
    body: CreateSessionRequest, _: Principal = Depends(require_operator)
) -> dict[str, str]:
    session_id = body.session_id or f"chat-{_short_id()}"
    async with session_scope() as session:
        agent = await session.get(AgentRow, body.agent_name)
        if agent is None:
            raise HTTPException(status_code=404, detail="agent not found")
        existing = await session.get(SessionRow, session_id)
        if existing is not None:
            raise HTTPException(status_code=409, detail="session already exists")
        session.add(
            SessionRow(session_id=session_id, name=body.name, agent_name=body.agent_name)
        )
    return {"session_id": session_id}


@router.put("/sessions/{session_id}")
async def update_session(
    session_id: str,
    body: UpdateSessionRequest,
    _: Principal = Depends(require_operator),
) -> dict[str, Any]:
    """Rename a chat (set ``title``) and/or pin it.

    Rename writes ``title`` — the same field ``_maybe_generate_session_title``
    populates — so a user-renamed chat is never relabelled by the first-turn
    auto-titler (it short-circuits when ``title`` is already set).
    """
    if not _ID_PATTERN.match(session_id):
        raise HTTPException(status_code=400, detail="invalid session_id")
    if body.title is None and body.pinned is None:
        raise HTTPException(status_code=400, detail="nothing to update")
    async with session_scope() as session:
        row = await session.get(SessionRow, session_id)
        if row is None:
            raise HTTPException(status_code=404, detail="session not found")
        if body.title is not None:
            row.title = body.title
        if body.pinned is not None:
            row.pinned = body.pinned
        row.updated_at = utcnow()
        view = _session_view(row)
    _publish_session_event(
        "chat.session_updated",
        session_id=session_id,
        title=view["title"],
        pinned=view["pinned"],
        agent_name=view["agent_name"],
    )
    return view


@router.delete("/sessions/{session_id}")
async def delete_session(
    session_id: str, _: Principal = Depends(require_operator)
) -> dict[str, bool]:
    """Hard-delete a chat and all of its messages.

    There are no DB-level foreign keys from ``session_memory`` / ``chat_turns``
    back to ``sessions``, so dependent rows are removed explicitly. Refuses
    (409) while a turn is actively streaming so the detached background task
    can't resurrect a just-deleted session. Promoted long-term memories are
    intentionally retained (they are durable knowledge, not the chat's
    transcript).
    """
    if not _ID_PATTERN.match(session_id):
        raise HTTPException(status_code=400, detail="invalid session_id")
    if broker_for_session(session_id) is not None:
        raise HTTPException(status_code=409, detail="a response is still streaming")
    async with session_scope() as session:
        row = await session.get(SessionRow, session_id)
        if row is None:
            raise HTTPException(status_code=404, detail="session not found")
        await session.execute(
            delete(SessionMemoryRow).where(SessionMemoryRow.session_id == session_id)
        )
        await session.execute(
            delete(ChatTurnRow).where(ChatTurnRow.session_id == session_id)
        )
        await session.delete(row)
    _publish_session_event("chat.session_deleted", session_id=session_id)
    return {"ok": True}


@router.get("/sessions/{session_id}/history")
async def session_history(
    session_id: str, _: Principal = Depends(require_viewer)
) -> list[dict[str, Any]]:
    if not _ID_PATTERN.match(session_id):
        raise HTTPException(status_code=400, detail="invalid session_id")
    async with session_scope() as session:
        row = await session.get(SessionRow, session_id)
        if row is None:
            raise HTTPException(status_code=404, detail="session not found")
    memory = SessionMemory(
        session_id=session_id,
        session_name=row.name,
        agent_name=row.agent_name,
        max_entries=500,
    )
    entries = await memory.recent(limit=MAX_HISTORY_LIMIT)
    return [{"kind": e.kind, "content": e.content} for e in entries]


@router.get("/sessions/{session_id}/active-turn")
async def session_active_turn(
    session_id: str, _: Principal = Depends(require_viewer)
) -> dict[str, Any] | None:
    """Return the currently-running turn for a session, if any.

    Used by clients that can't keep a WebSocket open (or that want to
    poll) to know whether a response is still being generated and show
    the partial accumulator.
    """
    if not _ID_PATTERN.match(session_id):
        raise HTTPException(status_code=400, detail="invalid session_id")
    async with session_scope() as session:
        stmt = (
            select(ChatTurnRow)
            .where(
                ChatTurnRow.session_id == session_id,
                ChatTurnRow.state == "running",
            )
            .order_by(ChatTurnRow.created_at.desc())
            .limit(1)
        )
        row = (await session.execute(stmt)).scalars().first()
    if row is None:
        return None
    citations: list[Any] = []
    if row.citations_json:
        try:
            citations = json.loads(row.citations_json)
        except Exception:
            citations = []
    return {
        "turn_id": row.turn_id,
        "state": row.state,
        "assistant_message": row.assistant_message,
        "citations": citations,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


async def reconcile_orphan_turns() -> int:
    """Mark any turn left ``running`` across a process restart as cancelled.

    Called from the web app's startup hook. Returns the number of rows
    updated. The corresponding SessionMemory has no assistant message
    for the lost turn, so the history reads as an unanswered question —
    which is the truth.
    """
    async with session_scope() as session:
        stmt = select(ChatTurnRow).where(ChatTurnRow.state == "running")
        rows = list((await session.execute(stmt)).scalars().all())
        for row in rows:
            row.state = "cancelled"
            row.error = "lost across server restart"
            row.finished_at = utcnow()
            row.updated_at = utcnow()
    if rows:
        log.info("chat_turns_reconciled", cancelled=len(rows))
    return len(rows)


@router.websocket("/ws/{session_id}")
async def chat_socket(ws: WebSocket, session_id: str) -> None:
    """Bidirectional chat socket.

    Authentication: the session cookie is carried automatically on same-origin
    upgrades; for tooling, a ``?token=<token>`` query parameter is accepted
    and compared with `secrets.compare_digest`. The session_id is validated
    against the DB before we enter the receive loop.
    """
    await ws.accept()

    # ID sanity check before anything else.
    if not _ID_PATTERN.match(session_id):
        await ws.close(code=1008, reason="invalid session_id")
        return

    auth = get_auth()
    # Token auth path (for headless clients / tests).
    query_token = ws.query_params.get("token", "")
    cookie_header = ws.cookies.get("spark_session")
    principal = None
    if query_token:
        if not secrets.compare_digest(query_token, auth.token):
            await ws.close(code=1008, reason="bad token")
            return
    elif cookie_header:
        try:
            principal = auth.verify_session(cookie_header)
        except Exception:
            await ws.close(code=1008, reason="bad session")
            return
    else:
        await ws.close(code=1008, reason="unauthenticated")
        return

    # Verify the session exists *up front* so an unauthenticated scan can't
    # probe arbitrary session_ids through the loop.
    async with session_scope() as session:
        srow = await session.get(SessionRow, session_id)
    if srow is None:
        await ws.close(code=1008, reason="session not found")
        return

    # If a turn is already running for this session (e.g. the operator
    # navigated away and came back, or opened another tab), replay its
    # current state and attach as a viewer before accepting new input.
    resume_broker = broker_for_session(session_id)
    if resume_broker is not None:
        await _send_resume(ws, resume_broker.turn_id)
        # Drain this turn concurrently with the message loop so the
        # operator sees remaining tokens live while still being able to
        # queue the next message after completion.
        viewer_task: asyncio.Task[None] | None = asyncio.create_task(
            _viewer_pump(ws, resume_broker)
        )
    else:
        viewer_task = None

    try:
        while True:
            raw = await ws.receive_text()
            if len(raw) > MAX_CHAT_TURN_CHARS * 2:
                await ws.send_json({"kind": "error", "content": "message too large"})
                continue
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                await ws.send_json({"kind": "error", "content": "invalid json"})
                continue
            try:
                turn = ChatTurnRequest.model_validate(parsed)
            except ValidationError:
                await ws.send_json({"kind": "error", "content": "invalid payload"})
                continue

            # Refuse to queue a second turn if one is already running —
            # the model isn't multi-turn-parallel and interleaving would
            # scramble the persisted assistant message.
            if broker_for_session(session_id) is not None:
                await ws.send_json(
                    {
                        "kind": "error",
                        "content": "a response is still streaming — wait for it to finish",
                    }
                )
                continue

            # Data-class guardrail on user input. Block → refuse turn;
            # redact → rewrite message in-place before it enters memory
            # and the model prompt. Runs first so a blocked prompt never
            # creates a turn row.
            try:
                ui_outcome = await apply_guardrails(
                    turn.content,
                    agent_name=srow.agent_name,
                    scope=DataScope.USER_INPUT,
                )
                safe_content = ui_outcome.text
            except SparkError as exc:
                # Send the structured error payload so the chat frontend
                # can render the FailureInspector. The legacy ``content``
                # field stays for backwards-compat: older clients keep
                # showing a thin error message; new clients see the
                # `error` dict and render the inspector.
                await ws.send_json(
                    {
                        "kind": "error",
                        "content": (
                            "Message refused by data-class guardrail: "
                            f"{exc.message}. Ask the operator to create a "
                            "grant or lower the class level in "
                            "Security Center → Data Classes."
                        ),
                        "error": exc.to_dict(),
                    }
                )
                continue

            # Persist the user turn eagerly so history is correct even if
            # the process dies before the assistant finishes.
            memory = SessionMemory(
                session_id=session_id,
                session_name=srow.name,
                agent_name=srow.agent_name,
                max_entries=500,
            )
            await memory.append(SessionEntry(kind="user", content=safe_content))

            turn_id = f"turn-{_short_id(16)}"
            broker = create_broker(turn_id, session_id)
            await ws.send_json(
                {
                    "kind": "started",
                    "data": {"turn_id": turn_id, "session_id": session_id},
                }
            )

            # Fire the background task — do NOT await it. The task keeps
            # running even if this websocket (or the whole page) goes
            # away. Viewers attach via the broker.
            asyncio.create_task(
                _run_turn_background(
                    turn_id=turn_id,
                    session_id=session_id,
                    agent_name=turn.agent_name,
                    user_message=safe_content,
                    session_memory=memory,
                    config=turn.context or ChatContextConfig(),
                )
            )

            # Cancel any previous viewer pump; attach to the fresh turn.
            if viewer_task is not None and not viewer_task.done():
                viewer_task.cancel()
            viewer_task = asyncio.create_task(_viewer_pump(ws, broker))
    except WebSocketDisconnect:
        return
    finally:
        if viewer_task is not None and not viewer_task.done():
            viewer_task.cancel()


async def _send_resume(ws: WebSocket, turn_id: str) -> None:
    """Tell a reconnecting viewer about the currently-running turn."""
    async with session_scope() as session:
        row = await session.get(ChatTurnRow, turn_id)
    if row is None:
        return
    citations: list[Any] = []
    if row.citations_json:
        try:
            citations = json.loads(row.citations_json)
        except Exception:  # pragma: no cover
            citations = []
    await ws.send_json(
        {
            "kind": "resume",
            "data": {
                "turn_id": turn_id,
                "state": row.state,
                "assistant_message": row.assistant_message,
                "citations": citations,
            },
        }
    )


async def _viewer_pump(ws: WebSocket, broker: Any) -> None:
    """Forward broker events to a WebSocket viewer until the turn ends.

    The task never raises; if the socket goes away we simply stop.
    """
    queue = broker.subscribe()
    try:
        while True:
            event: BrokerEvent = await queue.get()
            try:
                if event.kind == "token":
                    await ws.send_json({"kind": "token", "content": event.data})
                elif event.kind == "citations":
                    await ws.send_json({"kind": "citations", "memories": event.data})
                elif event.kind == "tool":
                    await ws.send_json({"kind": "tool", "data": event.data})
                elif event.kind == "tool_call":
                    await ws.send_json({"kind": "tool_call", "data": event.data})
                elif event.kind == "tool_result":
                    await ws.send_json({"kind": "tool_result", "data": event.data})
                elif event.kind == "error":
                    await ws.send_json({"kind": "error", "content": event.data})
                elif event.kind == "done":
                    await ws.send_json({"kind": "done", "data": event.data or {}})
                    return
            except Exception:
                # Client disconnected — stop forwarding; the background
                # task keeps going.
                return
    except asyncio.CancelledError:
        raise
    finally:
        broker.unsubscribe(queue)


async def _run_turn_background(
    *,
    turn_id: str,
    session_id: str,
    agent_name: str,
    user_message: str,
    session_memory: SessionMemory,
    config: ChatContextConfig,
) -> None:
    """Background task: own the turn lifecycle end-to-end.

    Independent of any WebSocket. Creates the ChatTurnRow, runs the
    model, flushes accumulator periodically, persists the final
    assistant message, publishes terminal event, and clears the broker.
    """
    broker = get_broker(turn_id)
    async with session_scope() as session:
        session.add(
            ChatTurnRow(
                turn_id=turn_id,
                session_id=session_id,
                agent_name=agent_name,
                state="running",
                user_message=user_message,
            )
        )

    final_text = ""
    try:
        final_text = await _run_chat_turn(
            turn_id=turn_id,
            broker=broker,
            agent_name=agent_name,
            user_message=user_message,
            session_memory=session_memory,
            config=config,
        )
        # Data-class guardrail on model output. Runs after streaming
        # so the operator has already seen tokens live; the persisted
        # version (history + turn row) reflects the redacted text. A
        # `block`-tier hit on output is unusual; surfaces as an error
        # suffix so the UI shows what happened.
        try:
            mo_outcome = await apply_guardrails(
                final_text,
                agent_name=agent_name,
                scope=DataScope.MODEL_OUTPUT,
            )
            final_text = mo_outcome.text
        except SparkError as exc:
            final_text = (
                final_text
                + "\n\n[guardrail] Output blocked: "
                + exc.message
            )
        await session_memory.append(
            SessionEntry(kind="assistant", content=final_text)
        )
        async with session_scope() as session:
            row = await session.get(ChatTurnRow, turn_id)
            if row is not None:
                row.state = "completed"
                row.assistant_message = final_text
                row.updated_at = utcnow()
                row.finished_at = utcnow()
        # First-turn title generation. Runs only once per session — if
        # the session already has a title, this is a no-op. Failures
        # are non-fatal (the UI just falls back to session_id).
        try:
            await _maybe_generate_session_title(
                session_id=session_id,
                agent_name=agent_name,
                user_message=user_message,
                assistant_message=final_text,
            )
        except Exception as exc:  # pragma: no cover — telemetry, never blocks
            log.warning("chat_title_generation_failed", error=str(exc))
        if broker is not None:
            broker.publish(
                BrokerEvent("done", {"session_id": session_id, "turn_id": turn_id})
            )
    except Exception as exc:  # pragma: no cover — surfaced to UI
        error_msg = f"Agent error: {type(exc).__name__}: {exc}"
        log.warning("chat_turn_failed", turn_id=turn_id, error=str(exc))
        # Record the error as the assistant message so history stays
        # coherent and the UI shows the failure inline.
        try:
            await session_memory.append(
                SessionEntry(kind="assistant", content=error_msg)
            )
        except Exception:
            pass
        async with session_scope() as session:
            row = await session.get(ChatTurnRow, turn_id)
            if row is not None:
                row.state = "error"
                row.error = error_msg
                row.assistant_message = final_text + error_msg
                row.updated_at = utcnow()
                row.finished_at = utcnow()
        if broker is not None:
            broker.publish(BrokerEvent("error", error_msg))
            broker.publish(
                BrokerEvent("done", {"session_id": session_id, "turn_id": turn_id})
            )
    finally:
        discard_broker(turn_id, session_id)


async def _run_chat_turn(
    *,
    turn_id: str,
    broker: Any,
    agent_name: str,
    user_message: str,
    session_memory: SessionMemory,
    config: ChatContextConfig,
) -> str:
    """Invoke the LLM for one chat turn, publishing tokens to the broker.

    Writes accumulated text to ChatTurnRow at most every
    ``_DB_FLUSH_MIN_INTERVAL_SEC`` so reconnecting viewers see recent
    output without every token forcing a DB write. Returns the full
    assistant response text; the caller is responsible for persisting
    the final value and publishing the ``done`` event.
    """
    from pathlib import Path  # noqa: PLC0415

    from spark.config.loader import load_agent  # noqa: PLC0415
    from spark.cost.tracker import CostTracker, record_usage  # noqa: PLC0415
    from spark.plugins.registry import default_registry  # noqa: PLC0415
    from spark.plugins.tool_runtime import BudgetGuard, ToolExecutor  # noqa: PLC0415
    from spark.providers.factory import build_chat_model  # noqa: PLC0415
    from spark.runtime import get_secret_manager  # noqa: PLC0415
    from spark.runtime.engine import _try_bind_tools  # noqa: PLC0415
    from spark.runtime.tool_spec import render_tools_block  # noqa: PLC0415
    from spark.utils.ids import short_id  # noqa: PLC0415

    agent_path = Path(f"~/.spark/agents/{agent_name}.yaml").expanduser()
    if not agent_path.exists():
        raise RuntimeError(f"agent YAML not found at {agent_path}")

    agent = load_agent(agent_path)
    mgr = get_secret_manager()
    chat_model = build_chat_model(agent.spec.runtime.provider, mgr)

    # ── Tool wiring ──────────────────────────────────────────────────
    # Bind every plugin in the agent's allowlist so the chat session can
    # actually call them. Without this, a model with a working YAML
    # ``plugins.allow`` block can only *talk about* tools — it has no
    # way to invoke them, so it tends to hallucinate "I wrote the file"
    # answers. The runtime engine already has the helper functions we
    # need (`_try_bind_tools`, `_extract_tool_call`,
    # `_make_tool_result_message`, `_classify_tool_error`) — we reuse
    # them here so chat and task runs share one tool-call protocol.
    plugin_allow = list(agent.spec.plugins.allow or [])
    plugin_registry = default_registry()
    tool_executor: ToolExecutor | None = None
    if plugin_allow:
        chat_model = _try_bind_tools(chat_model, plugin_allow, plugin_registry)
        budget_guard = BudgetGuard(
            # Chat is interactive so caps are tighter than a task run —
            # an out-of-control loop here would be visible to the user
            # and waste tokens fast. These can be raised per agent later
            # via runtime.max_* if needed.
            max_tool_calls=8,
            max_model_calls=12,
            max_iterations=12,
            max_tokens_per_run=None,
        )
        tool_executor = ToolExecutor(
            registry=plugin_registry,
            secrets=mgr,
            agent_spec=agent.spec,
            budget=budget_guard,
            agent_name=agent_name,
        )

    # Build the persona system prompt.
    system_prompt = (
        f"You are the {agent_name} agent. "
        f"{agent.spec.description or ''} "
        "Be concise, accurate, and helpful."
    )

    # Capability preamble — the model needs to be told what the runtime
    # provides, otherwise it falls back on training-time priors and
    # confidently denies having persistent memory, tools, etc.
    capability_lines: list[str] = []
    ltm_cfg = agent.spec.memory.long_term_memory
    ltm_enabled = bool(ltm_cfg and ltm_cfg.enabled)
    if ltm_enabled:
        capability_lines.append(
            "You have persistent long-term memory that survives across "
            "sessions. Relevant facts retrieved from it are surfaced below "
            "under 'Known context'. New facts worth keeping are extracted "
            "automatically after the conversation ends and stored for "
            "future recall — you do not need to ask the user to repeat "
            "things you have already learned."
        )
    capability_lines.append(
        "You also have short-term session memory — the recent turns of "
        "this conversation are included as message history below, so you "
        "can reference anything said earlier in this session."
    )
    if config.include_global and ltm_enabled:
        capability_lines.append(
            "Cross-agent shared memory is enabled for this turn, so some "
            "retrieved facts may originate from other agents in the same "
            "EmberSpark deployment."
        )
    if tool_executor is not None:
        capability_lines.append(
            "You have plugin tools wired in for this chat — use them when "
            "the user asks you to do something concrete (write a file, "
            "fetch a URL, search the web, etc). Do NOT pretend to call "
            "tools by emitting JSON in your text; emit a real tool call. "
            "Each tool result will be returned to you before you respond."
        )

    if capability_lines:
        system_prompt += "\n\nCapabilities:\n- " + "\n- ".join(capability_lines)

    if tool_executor is not None:
        # Render the tool schemas into the system prompt verbatim — this
        # is the same block the runtime engine adds for task runs, and
        # it works on every provider, including ones that don't accept
        # `bind_tools` (the model can still emit a `{"tool": ..., "args":
        # {...}}` JSON object in its content and `_extract_tool_call`
        # parses it round-trip).
        #
        # We also load the operator-stored config for each plugin and
        # pass it through so the model sees the real allow_paths,
        # allow_hosts, rules, etc. Without this the chat agent ends up
        # guessing common conventions ("I'll write to deliverables/
        # output/foo.md") and tripping PATH_DENIED on every first call
        # before the user has to spell out "the path is /data/spark-
        # volume/deliverables".
        plugin_configs = await _load_plugin_configs(plugin_allow, plugin_registry)
        system_prompt += "\n\n" + render_tools_block(
            plugin_allow, plugin_registry, configs=plugin_configs
        )

    # Load active persona if available.
    try:
        from spark.persistence.learning_models import PersonaRow  # noqa: PLC0415

        async with session_scope() as session:
            from sqlalchemy import select as _select  # noqa: PLC0415

            result = await session.execute(
                _select(PersonaRow).where(PersonaRow.is_active == True)  # noqa: E712
            )
            persona = result.scalars().first()
            if persona and persona.system_prompt:
                system_prompt = persona.system_prompt + "\n\n" + system_prompt
    except Exception:
        pass

    # Long-term memory retrieval — opt-in per turn via config.
    memory_context_block = ""
    citations: list[dict[str, Any]] = []
    if (
        config.include_long_term_memory
        and config.ltm_top_k > 0
        and agent.spec.memory.long_term_memory
        and agent.spec.memory.long_term_memory.enabled
    ):
        try:
            result = await _retrieve_memory_context(
                agent=agent,
                query=user_message,
                top_k=config.ltm_top_k,
                min_score=config.ltm_min_score,
                include_global=config.include_global,
                exclude_memory_ids=config.exclude_memory_ids,
                pin_memory_ids=config.pin_memory_ids,
                return_citations=True,
            )
            if isinstance(result, tuple):
                memory_context_block, citations = result
            else:
                memory_context_block = result
        except Exception as exc:  # pragma: no cover
            try:
                from spark.logging import get_logger  # noqa: PLC0415

                get_logger("spark.chat").warning(
                    "ltm_retrieval_failed", error=str(exc)
                )
            except Exception:
                pass

    if memory_context_block:
        system_prompt = (
            system_prompt
            + "\n\n"
            + memory_context_block
        )

    # Emit citations — both to the broker (live viewers) AND to the turn
    # row (reconnecting viewers fetching via resume).
    if citations:
        if broker is not None:
            broker.publish(BrokerEvent("citations", citations))
        try:
            async with session_scope() as session:
                row = await session.get(ChatTurnRow, turn_id)
                if row is not None:
                    row.citations_json = json.dumps(citations)
                    row.updated_at = utcnow()
        except Exception:  # pragma: no cover
            pass

    # Build message history from session memory.
    history = await session_memory.recent(limit=config.max_history_messages)
    messages: list[Any] = [("system", system_prompt)]
    for entry in history:
        if entry.kind == "user":
            messages.append(("human", entry.content))
        elif entry.kind == "assistant":
            messages.append(("ai", entry.content))

    # Try streaming first, fall back to non-streaming.
    full_response = ""
    last_flush = time.monotonic()

    async def _maybe_flush_db(force: bool = False) -> None:
        nonlocal last_flush
        now = time.monotonic()
        if not force and (now - last_flush) < _DB_FLUSH_MIN_INTERVAL_SEC:
            return
        last_flush = now
        try:
            async with session_scope() as session:
                row = await session.get(ChatTurnRow, turn_id)
                if row is not None:
                    row.assistant_message = full_response
                    row.updated_at = utcnow()
        except Exception:  # pragma: no cover — best effort
            pass

    # ── Cost tracking for chat ───────────────────────────────────────
    # Build a CostTracker with a synthetic ``chat-{turn_id}`` run id so
    # chat token spend lands in the same dashboards as task runs (the
    # Cost page sums across all CostEventRows regardless of source).
    # The ``task_name`` is None to distinguish chat rows visually; the
    # provider/model come straight from the agent config.
    provider_cfg = agent.spec.runtime.provider
    chat_run_id = f"chat-{turn_id}-{short_id()[:6]}"
    cost_tracker = CostTracker(
        run_id=chat_run_id,
        agent_name=agent_name,
        task_name=None,
        provider=provider_cfg.type,
        model=provider_cfg.model,
    )
    chat_api_key_ref = getattr(provider_cfg, "api_key_ref", None)
    chat_api_key: str | None = None
    if chat_api_key_ref:
        try:
            secret_value = mgr.get(chat_api_key_ref)
            chat_api_key = (
                secret_value.get_secret_value()
                if hasattr(secret_value, "get_secret_value")
                else str(secret_value)
            )
        except Exception:
            chat_api_key = None

    if tool_executor is None:
        # Pure-text path (no plugins allowlisted). Stream tokens; record
        # one cost row per turn from the final chunk's usage_metadata
        # (langchain-openai aggregates usage onto the last stream chunk).
        last_chunk: Any = None
        import time as _time  # noqa: PLC0415

        started_mono = _time.monotonic()
        started_wall = utcnow()
        try:
            async for chunk in chat_model.astream(messages):
                token = ""
                if hasattr(chunk, "content") and chunk.content:
                    token = str(chunk.content)
                elif isinstance(chunk, str):
                    token = chunk
                if token:
                    full_response += token
                    if broker is not None:
                        broker.publish(BrokerEvent("token", token))
                    await _maybe_flush_db()
                last_chunk = chunk
        except (NotImplementedError, AttributeError):
            result = await chat_model.ainvoke(messages)
            full_response = str(getattr(result, "content", result))
            last_chunk = result
            if broker is not None:
                broker.publish(BrokerEvent("token", full_response))
        await _record_chat_model_call(
            cost_tracker=cost_tracker,
            sequence=1,
            response=last_chunk,
            started_at=started_wall,
            started_mono=started_mono,
            api_key=chat_api_key,
        )
        await _flush_chat_cost_aggregate(cost_tracker)
        await _maybe_flush_db(force=True)
        return full_response or "(empty response)"

    # ── Tool-enabled path ────────────────────────────────────────────
    # Loop: invoke the model, run any tool call it requested, append the
    # result, repeat until the model produces a content-only response.
    # Stream only the *final* response — intermediate turns are short
    # tool-call payloads with no useful prose, so a single ainvoke per
    # round keeps the path simple.
    full_response = await _run_chat_tool_loop(
        chat_model=chat_model,
        tool_executor=tool_executor,
        cost_tracker=cost_tracker,
        api_key=chat_api_key,
        messages=messages,
        broker=broker,
        on_text=lambda token: _maybe_flush_db(),
        accumulator=lambda token: None,
    )
    await _flush_chat_cost_aggregate(cost_tracker)
    await _maybe_flush_db(force=True)
    return full_response or "(empty response)"


async def _record_chat_model_call(
    *,
    cost_tracker: Any,
    sequence: int,
    response: Any,
    started_at: Any,
    started_mono: float,
    api_key: str | None,
) -> None:
    """Record one model invocation for a chat turn.

    Mirror of the engine's per-call recording — same schema, same
    `model_call_events` table, same OpenRouter enrichment scheduling.
    Failures here are non-fatal: chat responses still surface to the
    user even if the cost row fails to persist.
    """
    try:
        from spark.cost.per_call import (  # noqa: PLC0415
            measure_latency_ms,
            record_model_call,
            schedule_openrouter_enrichment,
            split_usage,
        )

        # Also feed the in-memory accumulator so the run-aggregate
        # CostEventRow at turn-end has tokens to sum from when the
        # per-call rows haven't all flushed yet.
        breakdown = split_usage(response)
        if breakdown:
            cost_tracker.add(
                breakdown["input_tokens"], breakdown["output_tokens"]
            )

        result = await record_model_call(
            run_id=cost_tracker.run_id,
            sequence=sequence,
            provider=cost_tracker.provider,
            model=cost_tracker.model,
            response=response,
            started_at=started_at,
            finished_at=utcnow(),
            latency_ms=measure_latency_ms(started_mono),
        )
        if result is None:
            return
        row_id, request_id = result
        if (
            cost_tracker.provider == "openrouter"
            and isinstance(request_id, str)
            and request_id.startswith("gen-")
            and row_id
            and api_key
        ):
            schedule_openrouter_enrichment(
                row_id=row_id, request_id=request_id, api_key=api_key
            )
    except Exception as exc:  # pragma: no cover — telemetry never blocks chat
        log.warning("chat.model_call_record_failed", error=str(exc))


async def _maybe_generate_session_title(
    *,
    session_id: str,
    agent_name: str,
    user_message: str,
    assistant_message: str,
) -> None:
    """Generate a 5-word summary of the chat and store it on the session
    row — but only the first time around.

    Why first-turn-only: the operator picks a session by topic, not by
    whatever the latest sub-conversation drifted into. A stable label
    that names the original intent is better navigation than a churning
    one. The internal ``session_id`` (``chat-…``) stays the canonical
    identifier; the title is a UI affordance.

    Uses the agent's currently configured chat model so the title is
    generated by the same provider that ran the conversation. If the
    model doesn't support `with_structured_output`, falls back to
    plain `ainvoke` and trims the response.
    """
    from pathlib import Path  # noqa: PLC0415
    import re as _re  # noqa: PLC0415

    from spark.config.loader import load_agent  # noqa: PLC0415
    from spark.providers.factory import build_chat_model  # noqa: PLC0415
    from spark.runtime import get_secret_manager  # noqa: PLC0415

    # Skip if already titled.
    async with session_scope() as session:
        row = await session.get(SessionRow, session_id)
        if row is None or row.title:
            return

    agent_path = Path(f"~/.spark/agents/{agent_name}.yaml").expanduser()
    if not agent_path.exists():
        return
    agent = load_agent(agent_path)
    chat_model = build_chat_model(agent.spec.runtime.provider, get_secret_manager())

    # Compact prompt — feed both turns so the model has the full
    # context to compress. Hard length-cap so the response can't
    # blow the column width even if the model rambles.
    sys_prompt = (
        "You generate compact 5-word labels for chat conversations. "
        "Respond with the label only — no quotes, no period, no prefix "
        "like 'Title:'. 2 to 5 words, lowercase except proper nouns, "
        "no emoji."
    )
    user_prompt = (
        f"User said: {user_message[:600]}\n\n"
        f"Assistant replied: {assistant_message[:600]}\n\n"
        "Label this conversation."
    )
    try:
        resp = await chat_model.ainvoke(
            [("system", sys_prompt), ("human", user_prompt)]
        )
    except Exception as exc:
        log.info("chat_title_invoke_failed", error=str(exc))
        return

    raw = str(getattr(resp, "content", "") or "").strip()
    if not raw:
        return

    # Sanitize. Strip surrounding quotes / "Title:" prefix / trailing
    # punctuation, collapse whitespace, clip to 60 chars (well under
    # the 160-char column ceiling).
    title = raw.split("\n", 1)[0].strip()
    title = _re.sub(r'^(title|label)\s*[:\-]\s*', '', title, flags=_re.IGNORECASE)
    title = title.strip(" \"'`.").strip()
    title = _re.sub(r"\s+", " ", title)
    if not title:
        return
    # Hard cap at 5 words — the prompt asks for 2-5 but a model can overshoot —
    # then clip characters as a final guard against a pathological long token.
    title = " ".join(title.split()[:5])
    title = title[:60]

    async with session_scope() as session:
        row = await session.get(SessionRow, session_id)
        if row is None or row.title:
            return  # raced with another worker; let the first one win
        row.title = title
        row.updated_at = utcnow()

    # Fan out via the SSE bus so every open Chat tab refreshes its
    # sidebar + header without waiting for the per-WS `done` event.
    # This also covers the case where the user has the Chat page open
    # but navigated away from this specific session — the sidebar
    # still updates with the new title.
    try:
        from spark.web.events import get_bus  # noqa: PLC0415

        get_bus().publish(
            "chat.session_updated",
            session_id=session_id,
            title=title,
            agent_name=agent_name,
        )
    except Exception:  # pragma: no cover — bus is best-effort
        pass


async def _flush_chat_cost_aggregate(cost_tracker: Any) -> None:
    """Write a `cost_events` aggregate row for the chat turn so the
    Cost dashboard sums chat alongside task runs.
    """
    try:
        from spark.cost.tracker import record_usage  # noqa: PLC0415

        await record_usage(cost_tracker)
    except Exception as exc:  # pragma: no cover
        log.warning("chat.cost_aggregate_failed", error=str(exc))


async def _run_chat_tool_loop(
    *,
    chat_model: Any,
    tool_executor: Any,
    cost_tracker: Any,
    api_key: str | None,
    messages: list[Any],
    broker: Any,
    on_text: Any,
    accumulator: Any,
) -> str:
    """Drive the model until it stops emitting tool calls.

    Returns the final assistant content. Intermediate "the model wants
    to call X with args Y" turns are surfaced to the broker as
    ``tool_call`` and ``tool_result`` events so the UI can show what
    actually happened, while only the last (content-only) turn streams
    its tokens to the user. Each model call records a per-call
    ``model_call_events`` row so chat shows up in the cost dashboard
    alongside scheduled task runs; OpenRouter rows schedule the
    deferred enrichment fetch the same way the engine does.
    """
    import time as _time  # noqa: PLC0415

    full_response = ""
    sequence = 0
    max_rounds = tool_executor.budget.max_iterations
    for _ in range(max_rounds):
        try:
            tool_executor.budget.tick_iter()
            tool_executor.budget.tick_model()
        except Exception:  # pragma: no cover — budget guard already raises clean
            raise
        sequence += 1

        # Ask the model what to do next. Use ainvoke (not astream): the
        # model will either emit tool_calls (no useful prose to stream)
        # or a content-only final response (which we then re-stream
        # below in a second pass).
        started_mono = _time.monotonic()
        started_wall = utcnow()
        response = await chat_model.ainvoke(messages)
        await _record_chat_model_call(
            cost_tracker=cost_tracker,
            sequence=sequence,
            response=response,
            started_at=started_wall,
            started_mono=started_mono,
            api_key=api_key,
        )
        tool_call = _extract_tool_call(response)
        if tool_call is None:
            # Final answer — re-stream the content if the provider
            # supports it. If we already paid the latency for a full
            # ainvoke, there's no win in streaming; just emit the whole
            # text as one token so the UI doesn't stall.
            content = str(getattr(response, "content", "") or "")
            if content:
                full_response += content
                if broker is not None:
                    broker.publish(BrokerEvent("token", content))
            return full_response

        # Persist the assistant turn (with its tool_calls) so the next
        # invoke sees its own request — providers reject orphan tool
        # results otherwise.
        messages.append(response)
        plugin_name, args, tool_call_id = tool_call

        if broker is not None:
            broker.publish(
                BrokerEvent(
                    "tool_call",
                    {
                        "plugin": plugin_name,
                        "args": args,
                        "tool_call_id": tool_call_id,
                    },
                )
            )

        try:
            outcome = await tool_executor.call(plugin_name, args)
            body = json.dumps(outcome.filtered.content, default=str)
            is_error = False
            if broker is not None:
                broker.publish(
                    BrokerEvent(
                        "tool_result",
                        {
                            "plugin": plugin_name,
                            "tool_call_id": tool_call_id,
                            "content": outcome.filtered.content,
                            "is_error": False,
                        },
                    )
                )
        except Exception as exc:
            error_class = _classify_tool_error(exc)
            if isinstance(exc, SparkError):
                spark_err = exc
            else:
                spark_err = SparkError(
                    code=ErrorCode.PLUGIN_RAISED,
                    message=str(exc) or type(exc).__name__,
                    detail={"plugin": plugin_name},
                )
            structured = spark_err.to_dict()
            payload = {"error": structured}
            body = json.dumps(payload, default=str)
            is_error = True
            log.warning(
                "chat.tool_error",
                plugin=plugin_name,
                error_class=error_class,
                error_code=spark_err.code.value,
                error=str(exc),
            )
            if broker is not None:
                broker.publish(
                    BrokerEvent(
                        "tool_result",
                        {
                            "plugin": plugin_name,
                            "tool_call_id": tool_call_id,
                            "error": str(exc),
                            "error_class": error_class,
                            # Full SparkError payload so the chat frontend
                            # can render the FailureInspector beneath the
                            # thin ``✗ plugin: ...`` line.
                            "error_payload": structured,
                            "is_error": True,
                        },
                    )
                )
            try:
                from spark.errors.notify import notify_gate_failure  # noqa: PLC0415

                await notify_gate_failure(
                    spark_err, agent_name=agent_name, run_id=None
                )
            except Exception:  # pragma: no cover — best-effort
                pass

        messages.append(
            _make_tool_result_message(
                plugin_name=plugin_name,
                tool_call_id=tool_call_id,
                body=body,
                is_error=is_error,
            )
        )
    # Loop limit reached — return whatever we accumulated plus a hint.
    if not full_response:
        full_response = (
            f"(tool-call loop reached the {max_rounds}-iteration cap "
            "without a final answer)"
        )
    return full_response


async def _retrieve_memory_context(
    *,
    agent: Any,
    query: str,
    top_k: int,
    min_score: float,
    include_global: bool,
    exclude_memory_ids: list[str] | None = None,
    pin_memory_ids: list[str] | None = None,
    return_citations: bool = False,
) -> str | tuple[str, list[dict[str, Any]]]:
    """Retrieve relevant long-term memories and format as a text block.

    Queries the agent's own namespace plus any readable shared scopes
    (global, other-agent) as permitted by the agent's memory sharing
    config. Honors sensitivity gating.
    """
    from pathlib import Path  # noqa: PLC0415

    from spark.config.enums import PrivacyMode  # noqa: PLC0415
    from spark.memory.embeddings import SentenceTransformersProvider  # noqa: PLC0415
    from spark.memory.long_term import LongTermMemory  # noqa: PLC0415
    from spark.memory.retrieval import retrieve  # noqa: PLC0415

    ltm_cfg = agent.spec.memory.long_term_memory
    if ltm_cfg is None:
        return ""

    embedder = SentenceTransformersProvider(ltm_cfg.embedder.model)
    privacy_mode = PrivacyMode(agent.spec.runtime.privacy_mode)

    # Determine which namespaces to query.
    namespaces_to_query: list[tuple[str, str, str]] = [
        (ltm_cfg.namespace, ltm_cfg.collection, "self"),
    ]
    sharing = getattr(agent.spec.memory, "sharing", None)
    if include_global and sharing and sharing.read_global:
        namespaces_to_query.append(("__global__", "__global__", "global"))

    all_hits: list[Any] = []
    for ns, coll, scope_label in namespaces_to_query:
        try:
            ltm = LongTermMemory(
                namespace=ns,
                collection_name=coll,
                persist_path=Path(str(ltm_cfg.persist_path)).expanduser(),
                embedder=embedder,
            )
            hits = await retrieve(
                long_term=ltm,
                query=query,
                privacy_mode=privacy_mode,
                top_k=top_k,
                min_score=min_score,
                exclude_memory_ids=exclude_memory_ids,
                pin_memory_ids=pin_memory_ids,
            )
            for h in hits:
                all_hits.append((scope_label, h))
        except Exception:
            continue

    # Global-first dedup by memory_id. Split anti-patterns for
    # distinct framing in the prompt.
    seen: set[str] = set()
    all_hits.sort(key=lambda sh: getattr(sh[1], "score", 0.0), reverse=True)
    positive: list[tuple[str, Any]] = []
    anti: list[tuple[str, Any]] = []
    citations: list[dict[str, Any]] = []
    for scope_label, h in all_hits:
        mid = getattr(h, "memory_id", "")
        if not mid or mid in seen:
            continue
        seen.add(mid)
        if getattr(h, "is_anti_pattern", False):
            anti.append((scope_label, h))
        else:
            positive.append((scope_label, h))
        citations.append(
            {
                "memory_id": mid,
                "summary": getattr(h, "summary", ""),
                "memory_type": getattr(h, "memory_type", ""),
                "score": float(getattr(h, "score", 0.0)),
                "scope": scope_label,
                "is_anti_pattern": bool(getattr(h, "is_anti_pattern", False)),
            }
        )
        if len(positive) + len(anti) >= top_k:
            break

    # Record citation intent — the engine/chat path bumps
    # successful_citation_count after the run finishes.
    try:
        await _mark_citations(seen)
    except Exception:
        pass

    parts: list[str] = []
    if positive:
        parts.append("Known context:")
        for i, (scope_label, h) in enumerate(positive, start=1):
            parts.append(
                f"[M{i}] ({scope_label}/{getattr(h, 'memory_type', '?')}) {getattr(h, 'summary', '')}"
            )
    if anti:
        parts.append("\nKnown failure modes to avoid:")
        for i, (scope_label, h) in enumerate(anti, start=1):
            parts.append(
                f"[A{i}] ({scope_label}/{getattr(h, 'memory_type', '?')}) {getattr(h, 'summary', '')}"
            )

    block = "\n".join(parts)
    if return_citations:
        return block, citations
    return block


async def _mark_citations(memory_ids: set[str]) -> None:
    """Increment usage_count + last_cited_at for retrieved memories.

    Successful-citation bumps happen later, only if the resulting run
    succeeds. This is the cheap "was retrieved" counter.
    """
    if not memory_ids:
        return
    from sqlalchemy import select as _select  # noqa: PLC0415

    from spark.persistence.db import session_scope  # noqa: PLC0415
    from spark.persistence.models import LongTermMemoryIndexRow  # noqa: PLC0415
    from spark.utils.time import utcnow  # noqa: PLC0415

    async with session_scope() as session:
        result = await session.execute(
            _select(LongTermMemoryIndexRow).where(
                LongTermMemoryIndexRow.memory_id.in_(list(memory_ids))
            )
        )
        now = utcnow()
        for row in result.scalars().all():
            row.usage_count = (row.usage_count or 0) + 1
            row.last_cited_at = now
            session.add(row)


def _short_id(n: int = 8) -> str:
    return secrets.token_hex(n)[:n]
