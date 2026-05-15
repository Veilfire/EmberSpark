"""Slack plugin — post + react + search via the Slack Web API.

Implemented with httpx directly (no `slack_sdk` dep — Slack's REST
surface is small enough that ~150 lines of code is cheaper than
adding a 10MB dependency).

Two operator-controlled allowlists guard outbound calls:

- ``allow_channel_ids`` — channels the bot may post to. Empty refuses.
- ``allow_dm_user_ids`` — users the bot may DM. Empty refuses DMs.

The bot token (``xoxb-…``) handles posting + reactions + listing.
Searching messages requires a Slack user token, which the operator
configures separately under ``user_token_secret`` (optional). If a
user token isn't set, the ``search`` action returns an actionable
error.
"""

from __future__ import annotations

from typing import Any, ClassVar, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field

from spark.config.enums import Permission, Sensitivity
from spark.errors import ErrorCode, SparkError
from spark.plugins._http_base import (
    build_client,
    classify_connect_error,
    resolve_secret,
)


_SLACK_BASE = "https://slack.com/api"


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


class SlackConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bot_token_secret: str = Field(default="slack_bot_token", max_length=128)
    user_token_secret: str = Field(
        default="",
        max_length=128,
        description=(
            "Optional — required only if the agent uses search. Slack "
            "search.messages refuses bot tokens; a user token is the "
            "operator-installed account's auth."
        ),
    )
    allow_channel_ids: list[str] = Field(default_factory=list)
    allow_dm_user_ids: list[str] = Field(default_factory=list)
    default_parse_mode: Literal["mrkdwn", "plain"] = Field(default="mrkdwn")
    connect_timeout_seconds: float = Field(default=5.0, gt=0, le=60)
    read_timeout_seconds: float = Field(default=15.0, gt=0, le=120)
    verify_ssl: bool = True


# ---------------------------------------------------------------------------
# Action surface
# ---------------------------------------------------------------------------


class _SlackArgsWrapper(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal[
        "list_channels",
        "list_users",
        "post_message",
        "update_message",
        "react",
        "search_messages",
    ] = Field(
        description=(
            "Which Slack op. 'list_channels' / 'list_users' "
            "(discovery), 'post_message' (channel_id + text), "
            "'update_message' (channel + ts + text), 'react' (channel "
            "+ ts + emoji), 'search_messages' (requires user token)."
        ),
    )
    channel: str | None = Field(
        default=None,
        max_length=64,
        description="Slack channel id (or user id for IM).",
    )
    text: str | None = Field(default=None, max_length=40_000)
    ts: str | None = Field(default=None, max_length=64)
    emoji: str | None = Field(default=None, max_length=64)
    query: str | None = Field(default=None, max_length=512)
    parse_mode: Literal["mrkdwn", "plain"] | None = None


class SlackResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: str
    ok: bool
    ts: str | None = None
    channel: str | None = None
    channels: list[dict[str, Any]] | None = None
    users: list[dict[str, Any]] | None = None
    messages: list[dict[str, Any]] | None = None
    error: str | None = None


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


class SlackChannelEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    name: str
    is_member: bool = False
    is_private: bool = False
    is_general: bool = False
    risk: Literal["safe", "elevated", "danger"] = "safe"


class SlackUserEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    name: str
    real_name: str | None = None
    is_bot: bool = False
    is_admin: bool = False
    risk: Literal["safe", "elevated", "danger"] = "safe"


class SlackDiscovery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ok: bool
    error: str | None = None
    error_code: str | None = None
    error_detail: dict[str, Any] | None = None
    team: str | None = None
    user: str | None = None
    channels: list[SlackChannelEntry] = Field(default_factory=list)
    users: list[SlackUserEntry] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _channel_risk(channel: dict[str, Any]) -> Literal["safe", "elevated", "danger"]:
    name = (channel.get("name") or "").lower()
    if channel.get("is_general") or name in {"general", "announcements", "broadcast"}:
        return "elevated"
    if channel.get("is_private"):
        return "elevated"
    return "safe"


def _user_risk(user: dict[str, Any]) -> Literal["safe", "elevated", "danger"]:
    if user.get("is_admin") or user.get("is_owner") or user.get("is_primary_owner"):
        return "danger"
    return "safe"


async def _slack_call(
    client: httpx.AsyncClient,
    method: str,
    *,
    token: str,
    payload: dict[str, Any] | None = None,
    query: dict[str, str] | None = None,
) -> dict[str, Any]:
    url = f"{_SLACK_BASE}/{method}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "User-Agent": "spark-slack/0.1",
    }
    try:
        if payload is not None:
            headers["Content-Type"] = "application/json; charset=utf-8"
            resp = await client.post(url, headers=headers, json=payload)
        else:
            resp = await client.get(url, headers=headers, params=query)
    except httpx.RequestError as exc:
        raise classify_connect_error(exc, url=url, plugin_name="slack") from exc
    body = resp.text
    if not resp.is_success:
        if resp.status_code == 401:
            raise SparkError(
                ErrorCode.SECRET_NOT_FOUND,
                "slack: 401 — bot token rejected",
                detail={"plugin": "slack", "secret_name": "slack_bot_token"},
            )
        raise SparkError(
            ErrorCode.PLUGIN_RAISED,
            f"slack: HTTP {resp.status_code} from {method}: {body[:200]}",
            detail={"plugin": "slack", "method": method},
        )
    data = resp.json()
    if not data.get("ok"):
        err = data.get("error") or "unknown"
        if err in {"invalid_auth", "not_authed", "token_revoked"}:
            raise SparkError(
                ErrorCode.SECRET_NOT_FOUND,
                f"slack: {err}",
                detail={"plugin": "slack", "secret_name": "slack_bot_token"},
            )
        raise SparkError(
            ErrorCode.PLUGIN_RAISED,
            f"slack: {method} error: {err}",
            detail={"plugin": "slack", "method": method, "slack_error": err},
        )
    return data


def _refuse_channel(channel: str) -> SparkError:
    return SparkError(
        ErrorCode.PERMISSION_MISSING,
        f"slack: channel {channel!r} not in allow_channel_ids",
        detail={
            "plugin": "slack",
            "missing_allowlist_item": channel,
            "field": "allow_channel_ids",
        },
    )


def _refuse_dm_user(user: str) -> SparkError:
    return SparkError(
        ErrorCode.PERMISSION_MISSING,
        f"slack: user {user!r} not in allow_dm_user_ids",
        detail={
            "plugin": "slack",
            "missing_allowlist_item": user,
            "field": "allow_dm_user_ids",
        },
    )


# ---------------------------------------------------------------------------
# Plugin
# ---------------------------------------------------------------------------


class SlackPlugin:
    name: ClassVar[str] = "slack"
    version: ClassVar[str] = "0.1.0"
    description: ClassVar[str] = (
        "Post messages, react, and (with a user token) search Slack. "
        "Operator-locked per-channel + per-DM-user allowlist."
    )
    input_schema: ClassVar[type[BaseModel]] = _SlackArgsWrapper
    output_schema: ClassVar[type[BaseModel]] = SlackResult
    config_schema: ClassVar[type[BaseModel]] = SlackConfig
    required_permissions: ClassVar[frozenset[Permission]] = frozenset(
        {Permission.NET_HTTP, Permission.SECRETS_READ}
    )
    required_secrets: ClassVar[frozenset[str]] = frozenset()
    sensitivity: ClassVar[Sensitivity] = Sensitivity.MODERATE
    filter_output_before_model: ClassVar[bool] = True
    needs_network: ClassVar[bool] = True

    async def execute(self, args: _SlackArgsWrapper, ctx: Any) -> SlackResult:
        cfg = getattr(ctx, "plugin_config", {}) or {}
        bot_token = resolve_secret(
            cfg,
            config_key="bot_token_secret",
            default_secret_name="slack_bot_token",
            plugin_name="slack",
            ctx=ctx,
        )
        allow_channels = set(cfg.get("allow_channel_ids") or [])
        allow_dm_users = set(cfg.get("allow_dm_user_ids") or [])
        parse_default = cfg.get("default_parse_mode") or "mrkdwn"

        async with build_client(cfg) as client:
            if args.action == "list_channels":
                return await _do_list_channels(client, bot_token)
            if args.action == "list_users":
                return await _do_list_users(client, bot_token)
            if args.action in {"post_message", "update_message", "react"}:
                if not args.channel:
                    raise SparkError(
                        ErrorCode.INPUT_SCHEMA_INVALID,
                        f"slack: {args.action} requires channel",
                        detail={"plugin": "slack"},
                    )
                channel = args.channel
                # DMs (start with 'D' or 'U') vs channels (start with 'C' or 'G').
                if channel.startswith("U"):
                    if channel not in allow_dm_users:
                        raise _refuse_dm_user(channel)
                else:
                    if channel not in allow_channels:
                        raise _refuse_channel(channel)
                if args.action == "post_message":
                    return await _do_post_message(
                        client, bot_token, channel, args, parse_default
                    )
                if args.action == "update_message":
                    return await _do_update_message(
                        client, bot_token, channel, args, parse_default
                    )
                return await _do_react(client, bot_token, channel, args)
            if args.action == "search_messages":
                user_token_secret = (cfg.get("user_token_secret") or "").strip()
                if not user_token_secret:
                    raise SparkError(
                        ErrorCode.OPERATOR_OVERRIDE_REFUSED,
                        "slack: search_messages requires a user token (bot tokens can't search); set user_token_secret in plugin config",
                        detail={"plugin": "slack", "field": "user_token_secret"},
                    )
                user_token = resolve_secret(
                    cfg,
                    config_key="user_token_secret",
                    default_secret_name=user_token_secret,
                    plugin_name="slack",
                    ctx=ctx,
                )
                return await _do_search(client, user_token, args)
            raise SparkError(
                ErrorCode.INPUT_SCHEMA_INVALID,
                f"slack: unknown action {args.action!r}",
                detail={"plugin": "slack", "action": args.action},
            )


# ---------------------------------------------------------------------------
# Action implementations
# ---------------------------------------------------------------------------


async def _do_list_channels(
    client: httpx.AsyncClient, bot_token: str
) -> SlackResult:
    data = await _slack_call(
        client,
        "conversations.list",
        token=bot_token,
        query={"types": "public_channel,private_channel", "limit": "200"},
    )
    channels = [
        {
            "id": c.get("id"),
            "name": c.get("name"),
            "is_member": bool(c.get("is_member")),
            "is_private": bool(c.get("is_private")),
            "is_general": bool(c.get("is_general")),
        }
        for c in data.get("channels", [])
    ]
    return SlackResult(action="list_channels", ok=True, channels=channels)


async def _do_list_users(client: httpx.AsyncClient, bot_token: str) -> SlackResult:
    data = await _slack_call(
        client, "users.list", token=bot_token, query={"limit": "200"}
    )
    users = [
        {
            "id": u.get("id"),
            "name": u.get("name"),
            "real_name": u.get("real_name"),
            "is_bot": bool(u.get("is_bot")),
            "is_admin": bool(u.get("is_admin")),
        }
        for u in data.get("members", [])
        if not u.get("deleted")
    ]
    return SlackResult(action="list_users", ok=True, users=users)


async def _do_post_message(
    client: httpx.AsyncClient,
    bot_token: str,
    channel: str,
    args: _SlackArgsWrapper,
    parse_default: str,
) -> SlackResult:
    if not args.text:
        raise SparkError(
            ErrorCode.INPUT_SCHEMA_INVALID,
            "slack: post_message requires text",
            detail={"plugin": "slack"},
        )
    payload: dict[str, Any] = {"channel": channel, "text": args.text}
    if (args.parse_mode or parse_default) == "plain":
        payload["mrkdwn"] = False
    data = await _slack_call(client, "chat.postMessage", token=bot_token, payload=payload)
    return SlackResult(
        action="post_message",
        ok=True,
        channel=data.get("channel"),
        ts=data.get("ts"),
    )


async def _do_update_message(
    client: httpx.AsyncClient,
    bot_token: str,
    channel: str,
    args: _SlackArgsWrapper,
    parse_default: str,
) -> SlackResult:
    if not args.ts or not args.text:
        raise SparkError(
            ErrorCode.INPUT_SCHEMA_INVALID,
            "slack: update_message requires ts and text",
            detail={"plugin": "slack"},
        )
    payload: dict[str, Any] = {"channel": channel, "ts": args.ts, "text": args.text}
    if (args.parse_mode or parse_default) == "plain":
        payload["mrkdwn"] = False
    data = await _slack_call(client, "chat.update", token=bot_token, payload=payload)
    return SlackResult(
        action="update_message",
        ok=True,
        channel=data.get("channel"),
        ts=data.get("ts"),
    )


async def _do_react(
    client: httpx.AsyncClient,
    bot_token: str,
    channel: str,
    args: _SlackArgsWrapper,
) -> SlackResult:
    if not args.ts or not args.emoji:
        raise SparkError(
            ErrorCode.INPUT_SCHEMA_INVALID,
            "slack: react requires ts and emoji",
            detail={"plugin": "slack"},
        )
    emoji = args.emoji.strip(":")
    await _slack_call(
        client,
        "reactions.add",
        token=bot_token,
        payload={"channel": channel, "timestamp": args.ts, "name": emoji},
    )
    return SlackResult(action="react", ok=True, channel=channel, ts=args.ts)


async def _do_search(
    client: httpx.AsyncClient,
    user_token: str,
    args: _SlackArgsWrapper,
) -> SlackResult:
    if not args.query:
        raise SparkError(
            ErrorCode.INPUT_SCHEMA_INVALID,
            "slack: search_messages requires query",
            detail={"plugin": "slack"},
        )
    data = await _slack_call(
        client,
        "search.messages",
        token=user_token,
        query={"query": args.query, "count": "20"},
    )
    matches = data.get("messages", {}).get("matches", []) or []
    out = [
        {
            "channel": (m.get("channel") or {}).get("id"),
            "channel_name": (m.get("channel") or {}).get("name"),
            "user": m.get("user"),
            "username": m.get("username"),
            "text": m.get("text"),
            "ts": m.get("ts"),
            "permalink": m.get("permalink"),
        }
        for m in matches
    ]
    return SlackResult(action="search_messages", ok=True, messages=out)


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


async def discover(cfg: dict[str, Any], ctx: Any) -> SlackDiscovery:
    """Read-only Slack introspection used by the live-config editor.

    Calls auth.test + conversations.list + users.list with the bot
    token. Channels the bot isn't a member of are filtered out (bot
    can't post to them anyway).
    """
    try:
        bot_token = resolve_secret(
            cfg,
            config_key="bot_token_secret",
            default_secret_name="slack_bot_token",
            plugin_name="slack",
            ctx=ctx,
        )
    except SparkError as exc:
        return SlackDiscovery(
            ok=False,
            error=exc.message,
            error_code=exc.code.value,
            error_detail=exc.detail,
        )

    try:
        async with build_client(cfg) as client:
            auth = await _slack_call(client, "auth.test", token=bot_token)
            channels_data = await _slack_call(
                client,
                "conversations.list",
                token=bot_token,
                query={"types": "public_channel,private_channel", "limit": "200"},
            )
            users_data = await _slack_call(
                client, "users.list", token=bot_token, query={"limit": "200"}
            )
    except SparkError as exc:
        return SlackDiscovery(
            ok=False,
            error=exc.message,
            error_code=exc.code.value,
            error_detail=exc.detail,
        )

    channels = [
        SlackChannelEntry(
            id=c.get("id", ""),
            name=c.get("name", ""),
            is_member=bool(c.get("is_member")),
            is_private=bool(c.get("is_private")),
            is_general=bool(c.get("is_general")),
            risk=_channel_risk(c),
        )
        for c in channels_data.get("channels", [])
        if c.get("is_member")  # bot can only post where it's joined
    ]
    users = [
        SlackUserEntry(
            id=u.get("id", ""),
            name=u.get("name", ""),
            real_name=u.get("real_name"),
            is_bot=bool(u.get("is_bot")),
            is_admin=bool(u.get("is_admin")),
            risk=_user_risk(u),
        )
        for u in users_data.get("members", [])
        if not u.get("deleted") and not u.get("is_bot")
    ]
    return SlackDiscovery(
        ok=True,
        team=auth.get("team"),
        user=auth.get("user"),
        channels=channels,
        users=users,
    )
