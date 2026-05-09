"""Telegram-bot event source.

Long-polls Telegram's ``getUpdates`` API. Each new message in a
whitelisted chat fires the task with the inbound message as the
trigger payload.

Why long-poll instead of webhooks? Webhooks require a public callback
URL with valid TLS — a hassle for self-hosted EmberSpark. Long-polling
keeps the bot fully outbound.

Bot tokens live in the age vault (the agent YAML references the secret
*name*). Chat-ID whitelisting is enforced here so a stranger who DMs
the bot can't fire tasks.
"""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

import httpx

from spark.config.models import TelegramMessageEvent
from spark.logging import EventType, get_logger
from spark.runtime import get_secret_manager
from spark.secrets import SecretNotFound

log = get_logger("spark.scheduler.events.telegram_message")

_API_BASE = "https://api.telegram.org"


async def run_telegram_poller(
    task_name: str,
    event: TelegramMessageEvent,
    on_fire: Callable[[dict[str, Any]], Awaitable[None]],
) -> None:
    """Long-poll Telegram and fire ``on_fire`` per allowed message."""
    try:
        token = (
            get_secret_manager()
            .get(event.bot_token_secret)
            .get_secret_value()
        )
    except SecretNotFound:
        log.warning(
            "telegram.token_missing",
            task=task_name,
            secret=event.bot_token_secret,
        )
        return
    except Exception as exc:
        log.warning(
            "telegram.token_load_failed",
            task=task_name,
            error=str(exc),
        )
        return

    allowlist = set(event.allow_chat_ids)
    base = f"{_API_BASE}/bot{token}"
    last_update_id: int | None = None

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(
            connect=5.0,
            # read must accommodate the long-poll timeout + a small buffer.
            read=event.long_poll_timeout + 10.0,
            write=5.0,
            pool=5.0,
        ),
        verify=True,
        trust_env=False,
    ) as client:
        while True:
            params: dict[str, Any] = {
                "timeout": event.long_poll_timeout,
                "allowed_updates": ["message"],
            }
            if last_update_id is not None:
                params["offset"] = last_update_id + 1

            try:
                resp = await client.get(f"{base}/getUpdates", params=params)
                resp.raise_for_status()
                body = resp.json()
            except Exception as exc:
                log.warning("telegram.poll_failed", task=task_name, error=str(exc))
                try:
                    await asyncio.sleep(event.poll_seconds)
                except asyncio.CancelledError:
                    raise
                continue

            if not body.get("ok"):
                log.warning("telegram.api_error", task=task_name, body=body)
                try:
                    await asyncio.sleep(event.poll_seconds)
                except asyncio.CancelledError:
                    raise
                continue

            updates = body.get("result") or []
            for update in updates:
                update_id = update.get("update_id")
                if isinstance(update_id, int):
                    last_update_id = max(last_update_id or update_id, update_id)
                message = update.get("message")
                if not isinstance(message, dict):
                    continue
                chat = message.get("chat") or {}
                chat_id = chat.get("id")
                if allowlist and chat_id not in allowlist:
                    log.info(
                        "telegram.chat_blocked",
                        task=task_name,
                        chat_id=chat_id,
                    )
                    continue

                log.info(
                    "event_trigger.fire",
                    event_type=EventType.EVENT_TRIGGER_FIRED,
                    task=task_name,
                    source="telegram_message",
                    chat_id=chat_id,
                )
                try:
                    await on_fire(
                        {
                            "task": task_name,
                            "source": "telegram_message",
                            "message": message,
                        }
                    )
                except Exception as exc:
                    log.warning(
                        "telegram.on_fire_failed",
                        task=task_name,
                        error=str(exc),
                    )

            # Light defensive sleep between polls; long_poll_timeout
            # already gives us efficient blocking on the server side.
            try:
                await asyncio.sleep(event.poll_seconds)
            except asyncio.CancelledError:
                raise
