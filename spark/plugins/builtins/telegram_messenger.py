"""Telegram Bot API messenger plugin.

The agent uses this to send messages, edit them, and post inline
keyboards back to Telegram chats. Pairs with the Telegram event source
(``spark/scheduler/events/telegram_message.py``) so a chat can be
fully bidirectional.

Operator-locked:

- ``bot_token_secret`` — name of the secret in the age vault holding
  the BotFather token. The model never sees the token.
- ``allow_chat_ids`` — whitelist of chats the agent can send to.
  Empty list = plugin refuses to send. The agent cannot widen this.

The action surface is **scoped** — six methods that cover ~95% of
chat-bot use cases. We deliberately don't expose every Bot API method
(forwardMessage, copyMessage, sendPoll, sendDice, …) — the agent
doesn't need them and each adds attack surface. Operators who need
those can wire them through the generic ``http_client`` plugin.
"""

from __future__ import annotations

from typing import Any, ClassVar, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field

from spark.config.enums import Permission, Sensitivity

_API_BASE = "https://api.telegram.org"

#: Telegram refuses messages > 4096 chars. The plugin auto-splits on
#: ``send_message`` but rejects pre-truncated requests beyond a
#: pragmatic ceiling so the agent doesn't try to spam a giant payload.
_MAX_TEXT_PER_MESSAGE = 4096
_MAX_TOTAL_TEXT = _MAX_TEXT_PER_MESSAGE * 5  # up to 5 split messages


class TelegramMessengerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    bot_token_secret: str = Field(
        default="telegram_bot_token",
        max_length=128,
        description="Name of the secret in the age vault holding the bot token.",
    )
    allow_chat_ids: list[int] = Field(
        default_factory=list,
        description=(
            "Chats / groups the agent may send to. Required for any "
            "outbound message; empty list refuses everything."
        ),
    )
    parse_mode_default: Literal["MarkdownV2", "HTML", "plain"] = "MarkdownV2"
    timeout_seconds: float = Field(default=15.0, gt=0, le=60)


class _SendMessageArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["send_message"] = "send_message"
    chat_id: int
    text: str = Field(min_length=1, max_length=_MAX_TOTAL_TEXT)
    parse_mode: Literal["MarkdownV2", "HTML", "plain"] | None = None
    reply_to_message_id: int | None = None
    disable_notification: bool = False
    inline_keyboard: list[list[dict[str, str]]] | None = Field(
        default=None,
        description=(
            "Optional 2D array of inline buttons. Each button is "
            "``{text, callback_data}`` (≤ 64 byte payload) or "
            "``{text, url}`` (https only)."
        ),
    )


class _EditMessageArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["edit_message"] = "edit_message"
    chat_id: int
    message_id: int
    text: str = Field(min_length=1, max_length=_MAX_TEXT_PER_MESSAGE)
    parse_mode: Literal["MarkdownV2", "HTML", "plain"] | None = None


class _DeleteMessageArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["delete_message"] = "delete_message"
    chat_id: int
    message_id: int


class _SendChatActionArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["send_chat_action"] = "send_chat_action"
    chat_id: int
    chat_action: Literal[
        "typing",
        "upload_photo",
        "record_voice",
        "upload_voice",
        "upload_document",
        "find_location",
    ] = "typing"


class _AnswerCallbackArgs(BaseModel):
    """Acknowledge an inline-keyboard callback so Telegram stops the
    spinner. Required after handling a button press."""

    model_config = ConfigDict(extra="forbid")
    action: Literal["answer_callback"] = "answer_callback"
    callback_query_id: str = Field(min_length=1, max_length=128)
    text: str | None = Field(default=None, max_length=200)
    show_alert: bool = False


class _SetCommandsArgs(BaseModel):
    """Publish the bot's command list to Telegram so users get
    autocomplete in the message bar. Idempotent — call at startup."""

    model_config = ConfigDict(extra="forbid")
    action: Literal["set_commands"] = "set_commands"
    commands: list[dict[str, str]] = Field(
        max_length=100,
        description="List of {command, description} entries.",
    )


TelegramArgs = (
    _SendMessageArgs
    | _EditMessageArgs
    | _DeleteMessageArgs
    | _SendChatActionArgs
    | _AnswerCallbackArgs
    | _SetCommandsArgs
)


class _TelegramArgsWrapper(BaseModel):
    """Discriminated-union dispatch on ``action``."""

    model_config = ConfigDict(extra="forbid")
    action: Literal[
        "send_message",
        "edit_message",
        "delete_message",
        "send_chat_action",
        "answer_callback",
        "set_commands",
    ] = Field(
        description=(
            "Which Bot API call to make: 'send_message' / 'edit_message' / "
            "'delete_message' (chat_id required), 'send_chat_action' "
            "(typing indicator), 'answer_callback' (ack a button press), "
            "'set_commands' (publish slash-command list)."
        ),
    )
    # Everything past `action` is action-specific. We re-validate per
    # action with the typed inner model so the model gets clean
    # validation errors with exact field paths.
    chat_id: int | None = Field(
        default=None,
        description="Target chat (required for send/edit/delete/chat_action). Must be in the operator's allow_chat_ids.",
    )
    text: str | None = Field(
        default=None,
        description="Message text (required for send_message and edit_message).",
    )
    parse_mode: Literal["MarkdownV2", "HTML", "plain"] | None = Field(
        default=None,
        description="Markdown / HTML rendering mode. 'plain' disables formatting. Defaults to operator config.",
    )
    reply_to_message_id: int | None = Field(
        default=None,
        description="Quote this message in send_message replies.",
    )
    disable_notification: bool | None = Field(
        default=None,
        description="Send silently (no push notification on the recipient device).",
    )
    inline_keyboard: list[list[dict[str, str]]] | None = Field(
        default=None,
        description=(
            "Optional 2D array of inline buttons. Each button is "
            "{text, callback_data} (≤64-byte payload) or {text, url} (https only)."
        ),
    )
    message_id: int | None = Field(
        default=None,
        description="Target message (required for edit_message and delete_message).",
    )
    chat_action: Literal[
        "typing",
        "upload_photo",
        "record_voice",
        "upload_voice",
        "upload_document",
        "find_location",
    ] | None = Field(
        default=None,
        description="Visible activity indicator (used by send_chat_action). Telegram clears it after ~5s.",
    )
    callback_query_id: str | None = Field(
        default=None,
        description="Required by answer_callback. The id from the inbound callback_query update.",
    )
    show_alert: bool | None = Field(
        default=None,
        description="When acknowledging a callback, show a modal alert instead of a transient toast.",
    )
    commands: list[dict[str, str]] | None = Field(
        default=None,
        description="set_commands payload: list of {command, description} entries to publish to Telegram.",
    )


class TelegramMessengerResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: str
    ok: bool
    #: Set on send_message — the new message_id, useful for later edits.
    message_id: int | None = None
    #: Set on send_message when text was split — list of every msg_id.
    message_ids: list[int] | None = None
    error: str | None = None


class TelegramMessengerPlugin:
    name: ClassVar[str] = "telegram_messenger"
    version: ClassVar[str] = "0.1.0"
    description: ClassVar[str] = (
        "Telegram Bot API messenger — send / edit / delete messages, "
        "post inline keyboards, set commands. Operator-locked to a "
        "fixed allowlist of chats."
    )
    input_schema: ClassVar[type[BaseModel]] = _TelegramArgsWrapper
    output_schema: ClassVar[type[BaseModel]] = TelegramMessengerResponse
    config_schema: ClassVar[type[BaseModel]] = TelegramMessengerConfig
    required_permissions: ClassVar[frozenset[Permission]] = frozenset(
        {Permission.NET_HTTP, Permission.SECRETS_READ}
    )
    required_secrets: ClassVar[frozenset[str]] = frozenset()  # operator picks via config
    sensitivity: ClassVar[Sensitivity] = Sensitivity.MODERATE
    filter_output_before_model: ClassVar[bool] = True
    needs_network: ClassVar[bool] = True

    async def execute(
        self, args: _TelegramArgsWrapper, ctx: Any
    ) -> TelegramMessengerResponse:
        cfg = getattr(ctx, "plugin_config", {}) or {}
        bot_token_secret = cfg.get("bot_token_secret") or "telegram_bot_token"
        allow_chat_ids = set(int(c) for c in (cfg.get("allow_chat_ids") or []))
        parse_mode_default = cfg.get("parse_mode_default") or "MarkdownV2"
        timeout = float(cfg.get("timeout_seconds") or 15.0)

        secrets = getattr(ctx, "secrets", {}) or {}
        token = secrets.get(bot_token_secret)
        if not token:
            raise PermissionError(
                f"telegram_messenger: secret {bot_token_secret!r} not "
                "injected into context. Operator must declare it in the "
                "agent's required_secrets."
            )

        # Chat-id allowlist — applies to everything that targets a chat.
        # ``set_commands`` and ``answer_callback`` don't target a chat.
        if args.action in {
            "send_message",
            "edit_message",
            "delete_message",
            "send_chat_action",
        }:
            if args.chat_id is None:
                raise ValueError(
                    f"telegram_messenger: action {args.action!r} requires chat_id"
                )
            if not allow_chat_ids:
                raise PermissionError(
                    "telegram_messenger: operator has not allowlisted any "
                    "chat_ids — edit the plugin config"
                )
            if args.chat_id not in allow_chat_ids:
                raise PermissionError(
                    f"telegram_messenger: chat_id {args.chat_id} not in allowlist"
                )

        api_base = f"{_API_BASE}/bot{token}"
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(connect=5.0, read=timeout, write=5.0, pool=5.0),
            verify=True,
            trust_env=False,
        ) as client:
            return await _dispatch(
                args, client, api_base, parse_mode_default
            )


async def _dispatch(
    args: _TelegramArgsWrapper,
    client: httpx.AsyncClient,
    api_base: str,
    parse_mode_default: str,
) -> TelegramMessengerResponse:
    if args.action == "send_message":
        return await _send_message(args, client, api_base, parse_mode_default)
    if args.action == "edit_message":
        return await _edit_message(args, client, api_base, parse_mode_default)
    if args.action == "delete_message":
        return await _call(
            client,
            f"{api_base}/deleteMessage",
            {"chat_id": args.chat_id, "message_id": args.message_id},
            "delete_message",
        )
    if args.action == "send_chat_action":
        return await _call(
            client,
            f"{api_base}/sendChatAction",
            {"chat_id": args.chat_id, "action": args.chat_action or "typing"},
            "send_chat_action",
        )
    if args.action == "answer_callback":
        body: dict[str, Any] = {"callback_query_id": args.callback_query_id}
        if args.text:
            body["text"] = args.text
        if args.show_alert:
            body["show_alert"] = True
        return await _call(
            client, f"{api_base}/answerCallbackQuery", body, "answer_callback"
        )
    if args.action == "set_commands":
        return await _call(
            client,
            f"{api_base}/setMyCommands",
            {"commands": args.commands or []},
            "set_commands",
        )
    raise ValueError(f"telegram_messenger: unknown action {args.action!r}")


async def _send_message(
    args: _TelegramArgsWrapper,
    client: httpx.AsyncClient,
    api_base: str,
    parse_mode_default: str,
) -> TelegramMessengerResponse:
    text = args.text or ""
    parse_mode = args.parse_mode or parse_mode_default
    parts = _split_message(text)
    base_payload: dict[str, Any] = {
        "chat_id": args.chat_id,
        "disable_notification": bool(args.disable_notification),
    }
    if parse_mode != "plain":
        base_payload["parse_mode"] = parse_mode
    if args.reply_to_message_id is not None:
        base_payload["reply_to_message_id"] = args.reply_to_message_id

    sent_ids: list[int] = []
    for i, chunk in enumerate(parts):
        payload = dict(base_payload)
        payload["text"] = chunk
        # Reply target only on the first chunk; subsequent chunks chain.
        if i > 0:
            payload.pop("reply_to_message_id", None)
        # Inline keyboard goes on the *last* chunk so the user sees the
        # buttons under the final piece of context.
        if i == len(parts) - 1 and args.inline_keyboard:
            payload["reply_markup"] = {"inline_keyboard": args.inline_keyboard}
        resp = await _call(client, f"{api_base}/sendMessage", payload, "send_message")
        if not resp.ok:
            return TelegramMessengerResponse(
                action="send_message",
                ok=False,
                message_ids=sent_ids,
                error=resp.error,
            )
        if resp.message_id is not None:
            sent_ids.append(resp.message_id)

    return TelegramMessengerResponse(
        action="send_message",
        ok=True,
        message_id=sent_ids[0] if sent_ids else None,
        message_ids=sent_ids,
    )


async def _edit_message(
    args: _TelegramArgsWrapper,
    client: httpx.AsyncClient,
    api_base: str,
    parse_mode_default: str,
) -> TelegramMessengerResponse:
    payload: dict[str, Any] = {
        "chat_id": args.chat_id,
        "message_id": args.message_id,
        "text": args.text,
    }
    parse_mode = args.parse_mode or parse_mode_default
    if parse_mode != "plain":
        payload["parse_mode"] = parse_mode
    return await _call(client, f"{api_base}/editMessageText", payload, "edit_message")


async def _call(
    client: httpx.AsyncClient,
    url: str,
    payload: dict[str, Any],
    action: str,
) -> TelegramMessengerResponse:
    try:
        resp = await client.post(url, json=payload)
    except Exception as exc:
        return TelegramMessengerResponse(
            action=action, ok=False, error=f"network error: {exc}"
        )
    if resp.status_code != 200:
        return TelegramMessengerResponse(
            action=action,
            ok=False,
            error=f"HTTP {resp.status_code}: {resp.text[:200]}",
        )
    body = resp.json()
    if not body.get("ok"):
        return TelegramMessengerResponse(
            action=action,
            ok=False,
            error=str(body.get("description") or body),
        )
    result = body.get("result")
    msg_id = result.get("message_id") if isinstance(result, dict) else None
    return TelegramMessengerResponse(
        action=action, ok=True, message_id=msg_id
    )


def _split_message(text: str) -> list[str]:
    """Split long text on paragraph boundaries to fit Telegram's 4096-char limit.

    Single-character oversize text (e.g. a 5000-char log dump) is hard-cut.
    Paragraph-aware splitting keeps markdown / code blocks intact when
    possible.
    """
    if len(text) <= _MAX_TEXT_PER_MESSAGE:
        return [text]
    chunks: list[str] = []
    remaining = text
    while remaining:
        if len(remaining) <= _MAX_TEXT_PER_MESSAGE:
            chunks.append(remaining)
            break
        # Find the latest paragraph break under the limit.
        cut = remaining.rfind("\n\n", 0, _MAX_TEXT_PER_MESSAGE)
        if cut < _MAX_TEXT_PER_MESSAGE // 2:
            cut = remaining.rfind("\n", 0, _MAX_TEXT_PER_MESSAGE)
        if cut < _MAX_TEXT_PER_MESSAGE // 2:
            cut = _MAX_TEXT_PER_MESSAGE
        chunks.append(remaining[:cut])
        remaining = remaining[cut:].lstrip()
    return chunks
