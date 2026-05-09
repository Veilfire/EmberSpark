"""Telegram bot runner — full chatbot UX over the Bot API.

This is the big-brother of ``telegram_message.run_telegram_poller``.
That source was one-shot: message → fire task → silence. This one is a
real bot:

- **Long-polls** ``getUpdates`` (no public webhook URL needed; works
  behind NAT).
- **Routes** each message against an explicit binding list. Only chat
  IDs in ``bindings`` get answered; only user IDs in
  ``allow_user_ids`` (when set) can speak.
- **Parses commands** (``/help``, ``/runs``, ``/run …``, ``/cancel``,
  ``/whoami``, plus operator-defined commands).
- **Conversational mode**: non-command messages fire the bound agent
  as a one-shot with the message as the trigger payload, then send
  the planner's final response back to Telegram.
- **Long-op UX**: typing indicator while the task is running, plus a
  placeholder "thinking…" message that gets edited to the final
  response on completion.
- **HITL groundwork**: callback_query handling for inline-keyboard
  button presses (Approve / Reject for paused tasks). Full HITL
  integration with the notification system is left for a follow-up;
  this layer just plumbs callback events through.
- **Auto-publishes** the command list to Telegram via ``setMyCommands``
  at startup so users get autocomplete.

Security posture:

- The bot token never leaves the age vault — looked up via the
  ``bot_token_secret`` name.
- Per-user authorization runs *before* any task fires.
- Trigger payloads are sanitized (no arbitrary headers, no file
  uploads) — just structured message data.
"""

from __future__ import annotations

import asyncio
import shlex
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

import httpx

from spark.config.models import (
    TelegramBotEvent,
    TelegramChatBinding,
    TelegramMessageEvent,
)
from spark.logging import EventType, get_logger
from spark.runtime import get_secret_manager
from spark.secrets import SecretNotFound

log = get_logger("spark.scheduler.events.telegram_bot")

_API_BASE = "https://api.telegram.org"

#: Built-in commands every binding gets for free.
BUILTIN_COMMANDS = [
    ("help", "Show this help message"),
    ("runs", "Show the last few runs of the bound agent"),
    ("run", "Fire a registered task: /run <task_name> [args]"),
    ("cancel", "Stop a running task by run_id"),
    ("whoami", "Show your binding info (chat, agent, role)"),
]


def upconvert_legacy(legacy: TelegramMessageEvent) -> TelegramBotEvent:
    """Convert the old single-task TelegramMessageEvent to a binding-based one.

    The old form fires *one* task per message. Without an explicit
    agent we fall back to a ``conversational`` binding for every
    allowed chat that uses the task's own agent. The bot runner only
    sees TelegramBotEvent internally so callers don't have to branch.
    """
    from spark.config.models import TelegramChatBinding

    bindings = []
    for cid in legacy.allow_chat_ids or []:
        bindings.append(
            TelegramChatBinding(
                chat_id=cid,
                # The legacy form didn't bind a specific agent; the
                # event-source caller passes the task's agent in. The
                # runner accepts an empty string here and resolves it
                # at fire-time from the task spec.
                agent="",
                allow_user_ids=[],
                mode="conversational",
            )
        )
    return TelegramBotEvent(
        type="telegram_bot",
        bot_token_secret=legacy.bot_token_secret,
        bindings=bindings or [TelegramChatBinding(chat_id=0, agent="", mode="conversational")],
        commands=[],
        poll_seconds=legacy.poll_seconds,
        long_poll_timeout=legacy.long_poll_timeout,
        typing_indicator=True,
    )


@dataclass
class _Routed:
    """A parsed inbound message ready to act on."""

    chat_id: int
    user_id: int
    user_name: str
    message_id: int
    text: str
    binding: TelegramChatBinding
    is_command: bool
    command: str | None
    command_args: str | None


async def run_telegram_bot(
    task_name: str,
    event: TelegramBotEvent,
    on_fire: Callable[[dict[str, Any]], Awaitable[None]],
) -> None:
    """Long-poll Telegram and route inbound updates.

    ``on_fire`` is the scheduler's "fire this task with the given
    payload" callback. The bot runner uses it for the
    ``conversational`` mode (every non-command message becomes a task
    fire) and for ``/run <task>`` commands.
    """
    try:
        token = get_secret_manager().get(event.bot_token_secret).get_secret_value()
    except SecretNotFound:
        log.warning(
            "telegram_bot.token_missing",
            task=task_name,
            secret=event.bot_token_secret,
        )
        return
    except Exception as exc:
        log.warning(
            "telegram_bot.token_load_failed", task=task_name, error=str(exc)
        )
        return

    base = f"{_API_BASE}/bot{token}"
    bindings_by_chat = {b.chat_id: b for b in event.bindings}

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(
            connect=5.0,
            read=event.long_poll_timeout + 10.0,
            write=5.0,
            pool=5.0,
        ),
        verify=True,
        trust_env=False,
    ) as client:
        # Publish the command list once so Telegram users see autocomplete.
        await _publish_commands(client, base, event.commands)

        last_update_id: int | None = None
        while True:
            params: dict[str, Any] = {
                "timeout": event.long_poll_timeout,
                "allowed_updates": ["message", "callback_query"],
            }
            if last_update_id is not None:
                params["offset"] = last_update_id + 1

            try:
                resp = await client.get(f"{base}/getUpdates", params=params)
                resp.raise_for_status()
                body = resp.json()
            except Exception as exc:
                log.warning(
                    "telegram_bot.poll_failed", task=task_name, error=str(exc)
                )
                try:
                    await asyncio.sleep(event.poll_seconds)
                except asyncio.CancelledError:
                    raise
                continue

            if not body.get("ok"):
                log.warning("telegram_bot.api_error", task=task_name, body=body)
                try:
                    await asyncio.sleep(event.poll_seconds)
                except asyncio.CancelledError:
                    raise
                continue

            for update in body.get("result") or []:
                uid = update.get("update_id")
                if isinstance(uid, int):
                    last_update_id = max(last_update_id or uid, uid)

                if "callback_query" in update:
                    await _handle_callback_query(
                        update["callback_query"], client, base, bindings_by_chat
                    )
                    continue

                message = update.get("message")
                if not isinstance(message, dict):
                    continue
                routed = _route(message, bindings_by_chat)
                if routed is None:
                    continue

                # Fire-and-forget per message so a slow handler doesn't
                # block the polling loop.
                asyncio.create_task(
                    _handle_message(routed, client, base, event, task_name, on_fire)
                )

            try:
                await asyncio.sleep(event.poll_seconds)
            except asyncio.CancelledError:
                raise


def _route(
    message: dict[str, Any],
    bindings_by_chat: dict[int, TelegramChatBinding],
) -> _Routed | None:
    """Resolve an inbound message to a binding + parse command syntax."""
    chat = message.get("chat") or {}
    sender = message.get("from") or {}
    chat_id = chat.get("id")
    if not isinstance(chat_id, int):
        return None
    binding = bindings_by_chat.get(chat_id)
    if binding is None:
        log.info("telegram_bot.chat_blocked", chat_id=chat_id)
        return None

    user_id = sender.get("id")
    if not isinstance(user_id, int):
        return None
    if binding.allow_user_ids and user_id not in binding.allow_user_ids:
        log.info(
            "telegram_bot.user_blocked", chat_id=chat_id, user_id=user_id
        )
        return None

    text = message.get("text") or message.get("caption") or ""
    is_command = text.startswith("/")
    command: str | None = None
    command_args: str | None = None
    if is_command:
        first, _, rest = text[1:].partition(" ")
        # Telegram sends ``/cmd@botname`` in groups; strip the @suffix.
        first = first.split("@", 1)[0].lower()
        command = first
        command_args = rest.strip() or None

    return _Routed(
        chat_id=chat_id,
        user_id=user_id,
        user_name=sender.get("username") or sender.get("first_name") or str(user_id),
        message_id=message.get("message_id") or 0,
        text=text,
        binding=binding,
        is_command=is_command,
        command=command,
        command_args=command_args,
    )


async def _handle_message(
    r: _Routed,
    client: httpx.AsyncClient,
    base: str,
    event: TelegramBotEvent,
    task_name: str,
    on_fire: Callable[[dict[str, Any]], Awaitable[None]],
) -> None:
    """Dispatch one routed message — built-in command, run_task, or chat."""
    log.info(
        "event_trigger.fire",
        event_type=EventType.EVENT_TRIGGER_FIRED,
        task=task_name,
        source="telegram_bot",
        chat_id=r.chat_id,
        user_id=r.user_id,
        is_command=r.is_command,
    )

    # Built-ins shortcut — these reply directly without firing a task.
    if r.is_command and r.command in {"help", "runs", "whoami", "cancel"}:
        await _handle_builtin(r, client, base, event)
        return

    # Custom command lookup.
    custom = next((c for c in event.commands if c.command == r.command), None) if r.is_command else None

    # Decide what to fire:
    #  1. Custom command with action=run_task → fire that task.
    #  2. /run <task> [args] (built-in) → fire <task>.
    #  3. Conversational mode → fire the binding's agent on the
    #     ``task_name`` we were registered with, with the message as payload.
    fired_task = task_name
    payload: dict[str, Any] = {
        "source": "telegram_bot",
        "chat_id": r.chat_id,
        "user_id": r.user_id,
        "user_name": r.user_name,
        "message_id": r.message_id,
        "text": r.text,
    }

    if r.is_command and r.command == "run":
        # /run <task_name> [free-form args] — hard-gated by the
        # binding's ``allow_run_tasks`` list. Empty allowlist = refused.
        if not r.binding.allow_run_tasks:
            await _send(
                client,
                base,
                r.chat_id,
                "`/run` is disabled for this chat. The operator must "
                "set `allow_run_tasks` in the binding to enable it.",
            )
            return
        if not r.command_args:
            await _send(client, base, r.chat_id, "Usage: `/run <task_name> [args]`")
            return
        try:
            tokens = shlex.split(r.command_args)
        except ValueError:
            tokens = r.command_args.split()
        if not tokens:
            await _send(client, base, r.chat_id, "Usage: `/run <task_name> [args]`")
            return
        candidate = tokens[0]
        if candidate not in r.binding.allow_run_tasks:
            allowed = ", ".join(f"`{t}`" for t in r.binding.allow_run_tasks)
            await _send(
                client,
                base,
                r.chat_id,
                f"Task `{candidate}` is not in this chat's allowlist. Allowed: {allowed}",
            )
            return
        await _audit(
            actor=f"telegram:{r.user_id}",
            kind="telegram.run",
            target=candidate,
            diff={
                "chat_id": r.chat_id,
                "user_name": r.user_name,
                "args": tokens[1:],
            },
        )
        fired_task = candidate
        payload["args"] = tokens[1:]
        payload["command"] = "run"
    elif custom and custom.action == "run_task":
        if not custom.task:
            await _send(
                client,
                base,
                r.chat_id,
                f"`/{r.command}` is misconfigured — operator must set `task:`.",
            )
            return
        fired_task = custom.task
        payload["command"] = r.command
        payload["args"] = (r.command_args or "").split() if r.command_args else []
    elif r.is_command and not custom:
        await _send(
            client,
            base,
            r.chat_id,
            f"Unknown command `/{r.command}`. Try `/help` for the list.",
        )
        return
    else:
        # Conversational mode (non-command) — only honoured if the
        # binding allows it.
        if r.binding.mode != "conversational":
            return  # silently ignore

    if event.typing_indicator:
        await _send_chat_action(client, base, r.chat_id, "typing")

    placeholder_id = await _send(
        client,
        base,
        r.chat_id,
        "_thinking…_",
        reply_to_message_id=r.message_id,
    )
    payload["placeholder_message_id"] = placeholder_id

    # The actual fire — scheduler handles it asynchronously. The
    # planner can use the ``telegram_messenger`` plugin (with
    # ``edit_message`` action) on ``placeholder_message_id`` to update
    # the placeholder with progress / final answer. The agent reads
    # ``trigger_payload`` from its first system prompt.
    try:
        await on_fire({"task": fired_task, "telegram": payload})
    except Exception as exc:
        log.warning(
            "telegram_bot.fire_failed",
            task=fired_task,
            chat_id=r.chat_id,
            error=str(exc),
        )
        await _edit(
            client, base, r.chat_id, placeholder_id, f"⚠️ Failed to fire: {exc}"
        )


async def _handle_builtin(
    r: _Routed,
    client: httpx.AsyncClient,
    base: str,
    event: TelegramBotEvent,
) -> None:
    """Reply to one of the always-on built-in commands."""
    if r.command == "help":
        text = _format_help(event)
    elif r.command == "whoami":
        # The user_name comes from Telegram's `from.username` /
        # `first_name` and can contain markdown specials. Wrap it in a
        # backtick code-span and escape any literal backticks the user
        # might've put in their nickname.
        safe_name = r.user_name.replace("`", "ʼ")
        text = (
            f"chat_id: `{r.chat_id}`\n"
            f"user_id: `{r.user_id}`\n"
            f"user: `{safe_name}`\n"
            f"agent: `{r.binding.agent or '(unbound)'}`\n"
            f"mode: `{r.binding.mode}`"
        )
    elif r.command == "runs":
        text = await _format_recent_runs(r.binding.agent)
    elif r.command == "cancel":
        if not r.binding.allow_cancel:
            text = (
                "`/cancel` is disabled for this chat. The operator must "
                "set `allow_cancel: true` in the binding to enable it."
            )
        elif not r.command_args:
            text = "Usage: `/cancel <run_id>`"
        else:
            text = await _cancel_run(
                r.command_args.strip(),
                allowed_agent=r.binding.agent,
                actor=f"telegram:{r.user_id}",
                chat_id=r.chat_id,
            )
    else:
        text = "Unknown command."
    await _send(client, base, r.chat_id, text, reply_to_message_id=r.message_id)


def _format_help(event: TelegramBotEvent) -> str:
    parts = ["*Built-in commands:*"]
    for cmd, desc in BUILTIN_COMMANDS:
        parts.append(f"  `/{cmd}` — {desc}")
    if event.commands:
        parts.append("")
        parts.append("*Custom commands:*")
        for c in event.commands:
            parts.append(f"  `/{c.command}` — {c.description}")
    parts.append("")
    parts.append("Send any non-command message to chat with the bound agent.")
    return "\n".join(parts)


async def _format_recent_runs(agent: str) -> str:
    """Read the last 5 runs for an agent and format a compact summary."""
    try:
        from sqlalchemy import select  # noqa: PLC0415

        from spark.persistence.db import session_scope  # noqa: PLC0415
        from spark.persistence.models import TaskRunRow  # noqa: PLC0415

        async with session_scope() as session:
            stmt = select(TaskRunRow)
            if agent:
                stmt = stmt.where(TaskRunRow.agent_name == agent)
            stmt = stmt.order_by(TaskRunRow.started_at.desc()).limit(5)
            rows = list((await session.execute(stmt)).scalars().all())
        if not rows:
            return "_No runs yet._"
        lines = ["*Recent runs:*"]
        for r in rows:
            short_id = r.run_id[-12:]
            line = (
                f"  `{short_id}` — {r.task_name} — {r.state}"
                + (f" — {r.iterations}it" if r.iterations else "")
            )
            lines.append(line)
        return "\n".join(lines)
    except Exception as exc:
        return f"_Could not read runs: {exc}_"


async def _cancel_run(
    run_id: str,
    *,
    allowed_agent: str,
    actor: str,
    chat_id: int,
) -> str:
    """Cancel a run *iff* it belongs to the binding's agent.

    Cross-agent cancels are refused at this boundary so a chat bound
    to one agent can't reach across and stop another agent's work.
    """
    try:
        from spark.persistence.db import session_scope  # noqa: PLC0415
        from spark.persistence.models import TaskRunRow  # noqa: PLC0415

        async with session_scope() as session:
            row = await session.get(TaskRunRow, run_id)
            if row is None:
                return f"_Run `{run_id}` not found._"
            # Per-binding scope check: the run must belong to the same
            # agent as the binding. Empty `allowed_agent` (legacy
            # bindings without an explicit agent) refuses everything.
            if not allowed_agent or row.agent_name != allowed_agent:
                return (
                    f"_Run `{run_id}` is not under this chat's bound "
                    f"agent (`{allowed_agent or 'unbound'}`). "
                    "Cancel refused._"
                )
            if row.state in ("completed", "failed", "stopped"):
                return f"_Run `{run_id}` is already {row.state}._"
            row.state = "stopped"
            row.error = "cancelled via Telegram /cancel"
        await _audit(
            actor=actor,
            kind="telegram.cancel",
            target=run_id,
            diff={"chat_id": chat_id, "agent": allowed_agent},
        )
        return f"✅ Cancelled run `{run_id}`."
    except Exception as exc:
        return f"⚠️ Cancel failed: {exc}"


async def _handle_callback_query(
    cq: dict[str, Any],
    client: httpx.AsyncClient,
    base: str,
    bindings_by_chat: dict[int, TelegramChatBinding],
) -> None:
    """Acknowledge an inline-keyboard button press.

    Full HITL plumbing (matching ``data`` to a paused approval row,
    flipping its state) is a follow-up. For now we acknowledge the
    button so Telegram stops the spinner and log the event so
    operators can see button presses landing.
    """
    cq_id = cq.get("id")
    data = cq.get("data") or ""
    sender = cq.get("from") or {}
    chat_id = (cq.get("message") or {}).get("chat", {}).get("id")
    log.info(
        "telegram_bot.callback_query",
        chat_id=chat_id,
        user_id=sender.get("id"),
        data=data,
    )
    if cq_id:
        try:
            await client.post(
                f"{base}/answerCallbackQuery",
                json={"callback_query_id": cq_id, "text": "Got it."},
            )
        except Exception:  # pragma: no cover
            pass


async def _publish_commands(
    client: httpx.AsyncClient,
    base: str,
    custom: list[Any],
) -> None:
    """Idempotent push of the bot's command list to Telegram so the
    user sees autocomplete in the message bar."""
    commands = [{"command": c, "description": d} for c, d in BUILTIN_COMMANDS]
    for c in custom or []:
        commands.append({"command": c.command, "description": c.description})
    try:
        await client.post(f"{base}/setMyCommands", json={"commands": commands})
    except Exception as exc:  # pragma: no cover
        log.warning("telegram_bot.setmycommands_failed", error=str(exc))


async def _send(
    client: httpx.AsyncClient,
    base: str,
    chat_id: int,
    text: str,
    *,
    reply_to_message_id: int | None = None,
) -> int | None:
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown",
    }
    if reply_to_message_id is not None:
        payload["reply_to_message_id"] = reply_to_message_id
    try:
        resp = await client.post(f"{base}/sendMessage", json=payload)
        body = resp.json()
        if body.get("ok"):
            return (body.get("result") or {}).get("message_id")
    except Exception as exc:  # pragma: no cover
        log.warning("telegram_bot.send_failed", chat_id=chat_id, error=str(exc))
    return None


async def _edit(
    client: httpx.AsyncClient,
    base: str,
    chat_id: int,
    message_id: int | None,
    text: str,
) -> None:
    if message_id is None:
        return
    try:
        await client.post(
            f"{base}/editMessageText",
            json={
                "chat_id": chat_id,
                "message_id": message_id,
                "text": text,
                "parse_mode": "Markdown",
            },
        )
    except Exception as exc:  # pragma: no cover
        log.warning("telegram_bot.edit_failed", chat_id=chat_id, error=str(exc))


async def _send_chat_action(
    client: httpx.AsyncClient, base: str, chat_id: int, action: str
) -> None:
    try:
        await client.post(
            f"{base}/sendChatAction", json={"chat_id": chat_id, "action": action}
        )
    except Exception:  # pragma: no cover
        pass


async def _audit(
    *, actor: str, kind: str, target: str, diff: dict[str, Any]
) -> None:
    """Write an audit row for a security-relevant Telegram bot action.

    Best-effort — never raises. Failures are logged but the user-facing
    response continues.
    """
    try:
        from spark.persistence.db import session_scope  # noqa: PLC0415
        from spark.persistence.learning_repos import (  # noqa: PLC0415
            AuditRepository,
        )

        async with session_scope() as session:
            await AuditRepository(session).append(
                actor=actor,
                kind=kind,
                target=target,
                diff=diff,
                severity="elevated",
            )
    except Exception as exc:  # pragma: no cover — best-effort
        log.warning("telegram_bot.audit_failed", kind=kind, error=str(exc))
