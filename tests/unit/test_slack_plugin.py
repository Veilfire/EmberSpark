"""Slack plugin — config, allowlist gates, error mapping."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from spark.errors.codes import ErrorCode, SparkError
from spark.plugins.builtins.slack import (
    SlackConfig,
    SlackPlugin,
    _SlackArgsWrapper,
    _channel_risk,
    _refuse_channel,
    _refuse_dm_user,
    _user_risk,
    discover,
)


def _ctx(secrets, cfg):
    class _Ctx:
        pass
    ctx = _Ctx()
    ctx.secrets = secrets
    ctx.plugin_config = cfg
    return ctx


def _w(action: str, **kw):
    return _SlackArgsWrapper(action=action, **kw)


def _resp(status: int, body: dict):
    import json
    r = httpx.Response(status_code=status, content=json.dumps(body).encode())
    return r


def _patch_request(responses_by_url: dict[str, httpx.Response]):
    async def fake(self, method, url, *a, **kw):  # noqa: ANN001
        for needle, resp in responses_by_url.items():
            if needle in url:
                return resp
        return _resp(404, {"ok": False, "error": "not_found"})

    return patch.object(httpx.AsyncClient, "request", new=fake)


# ---------------------------------------------------------------------------
# Risk classification + config
# ---------------------------------------------------------------------------


def test_channel_risk():
    assert _channel_risk({"name": "random"}) == "safe"
    assert _channel_risk({"name": "general", "is_general": True}) == "elevated"
    assert _channel_risk({"name": "team", "is_private": True}) == "elevated"
    assert _channel_risk({"name": "announcements"}) == "elevated"


def test_user_risk():
    assert _user_risk({"name": "alice"}) == "safe"
    assert _user_risk({"name": "owner", "is_admin": True}) == "danger"
    assert _user_risk({"name": "po", "is_primary_owner": True}) == "danger"


def test_config_defaults():
    cfg = SlackConfig()
    assert cfg.bot_token_secret == "slack_bot_token"
    assert cfg.user_token_secret == ""
    assert cfg.allow_channel_ids == []
    assert cfg.allow_dm_user_ids == []
    assert cfg.default_parse_mode == "mrkdwn"


# ---------------------------------------------------------------------------
# Refusal shapes wire to the Failure Inspector
# ---------------------------------------------------------------------------


def test_refuse_channel_emits_inspector_options():
    err = _refuse_channel("C12345")
    payload = err.to_dict()
    actionable = [t for t in payload["tuning"] if t["deep_link"]]
    assert actionable
    first = actionable[0]
    assert first["prefill"]["kind"] == "plugin_allowlist_grant"
    assert first["prefill"]["plugin"] == "slack"
    assert first["prefill"]["add_item"] == "C12345"
    assert first["prefill"]["field"] == "allow_channel_ids"


def test_refuse_dm_emits_inspector_options():
    err = _refuse_dm_user("U987")
    payload = err.to_dict()
    actionable = [t for t in payload["tuning"] if t["deep_link"]]
    assert actionable
    assert actionable[0]["prefill"]["field"] == "allow_dm_user_ids"


# ---------------------------------------------------------------------------
# Execute — refusal paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_refuses_when_secret_missing():
    plugin = SlackPlugin()
    ctx = _ctx({}, {})
    with pytest.raises(SparkError) as exc:
        await plugin.execute(_w("list_channels"), ctx)
    assert exc.value.code is ErrorCode.SECRET_NOT_FOUND


@pytest.mark.asyncio
async def test_post_refuses_when_channel_not_allowed():
    plugin = SlackPlugin()
    ctx = _ctx(
        {"slack_bot_token": "xoxb-tok"},
        {"allow_channel_ids": ["C111"]},
    )
    with pytest.raises(SparkError) as exc:
        await plugin.execute(
            _w("post_message", channel="C999", text="hi"), ctx
        )
    assert exc.value.code is ErrorCode.PERMISSION_MISSING
    assert exc.value.detail["missing_allowlist_item"] == "C999"
    assert exc.value.detail["field"] == "allow_channel_ids"


@pytest.mark.asyncio
async def test_dm_refuses_when_user_not_allowed():
    plugin = SlackPlugin()
    ctx = _ctx(
        {"slack_bot_token": "xoxb-tok"},
        {"allow_dm_user_ids": []},
    )
    with pytest.raises(SparkError) as exc:
        await plugin.execute(
            _w("post_message", channel="U123", text="hi"), ctx
        )
    assert exc.value.code is ErrorCode.PERMISSION_MISSING
    assert exc.value.detail["missing_allowlist_item"] == "U123"
    assert exc.value.detail["field"] == "allow_dm_user_ids"


@pytest.mark.asyncio
async def test_post_message_happy_path():
    plugin = SlackPlugin()
    ctx = _ctx(
        {"slack_bot_token": "xoxb-tok"},
        {"allow_channel_ids": ["C111"]},
    )
    with _patch_request(
        {
            "chat.postMessage": _resp(
                200, {"ok": True, "channel": "C111", "ts": "1700000000.001"}
            )
        }
    ):
        r = await plugin.execute(
            _w("post_message", channel="C111", text="hello"), ctx
        )
    assert r.ok is True
    assert r.ts == "1700000000.001"


@pytest.mark.asyncio
async def test_post_message_missing_text():
    plugin = SlackPlugin()
    ctx = _ctx(
        {"slack_bot_token": "xoxb-tok"},
        {"allow_channel_ids": ["C111"]},
    )
    with pytest.raises(SparkError) as exc:
        await plugin.execute(_w("post_message", channel="C111"), ctx)
    assert exc.value.code is ErrorCode.INPUT_SCHEMA_INVALID


@pytest.mark.asyncio
async def test_react_requires_ts_and_emoji():
    plugin = SlackPlugin()
    ctx = _ctx(
        {"slack_bot_token": "xoxb-tok"},
        {"allow_channel_ids": ["C111"]},
    )
    with pytest.raises(SparkError) as exc:
        await plugin.execute(_w("react", channel="C111"), ctx)
    assert exc.value.code is ErrorCode.INPUT_SCHEMA_INVALID


@pytest.mark.asyncio
async def test_search_refused_without_user_token():
    plugin = SlackPlugin()
    ctx = _ctx({"slack_bot_token": "xoxb-tok"}, {})
    with pytest.raises(SparkError) as exc:
        await plugin.execute(_w("search_messages", query="foo"), ctx)
    assert exc.value.code is ErrorCode.OPERATOR_OVERRIDE_REFUSED
    assert exc.value.detail["field"] == "user_token_secret"


@pytest.mark.asyncio
async def test_slack_api_invalid_auth_maps_to_secret_not_found():
    plugin = SlackPlugin()
    ctx = _ctx(
        {"slack_bot_token": "bad"},
        {"allow_channel_ids": ["C111"]},
    )
    with _patch_request(
        {"chat.postMessage": _resp(200, {"ok": False, "error": "invalid_auth"})}
    ):
        with pytest.raises(SparkError) as exc:
            await plugin.execute(
                _w("post_message", channel="C111", text="hi"), ctx
            )
    assert exc.value.code is ErrorCode.SECRET_NOT_FOUND


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_discover_returns_error_when_secret_missing():
    result = await discover({}, _ctx({}, {}))
    assert result.ok is False
    assert result.error_code == ErrorCode.SECRET_NOT_FOUND.value


@pytest.mark.asyncio
async def test_discover_happy_path():
    ctx = _ctx({"slack_bot_token": "xoxb-tok"}, {})
    with _patch_request(
        {
            "auth.test": _resp(200, {"ok": True, "team": "Acme", "user": "spark-bot"}),
            "conversations.list": _resp(
                200,
                {
                    "ok": True,
                    "channels": [
                        {
                            "id": "C001",
                            "name": "general",
                            "is_member": True,
                            "is_general": True,
                        },
                        {
                            "id": "C002",
                            "name": "random",
                            "is_member": True,
                        },
                        {
                            "id": "C003",
                            "name": "off-limits",
                            "is_member": False,  # bot not a member — should be filtered out
                        },
                    ],
                },
            ),
            "users.list": _resp(
                200,
                {
                    "ok": True,
                    "members": [
                        {"id": "U001", "name": "alice", "is_admin": True},
                        {"id": "U002", "name": "bob"},
                        {
                            "id": "B001",
                            "name": "spark-bot",
                            "is_bot": True,  # bots filtered
                        },
                    ],
                },
            ),
        }
    ):
        result = await discover({}, ctx)
    assert result.ok is True
    assert result.team == "Acme"
    ch_ids = [c.id for c in result.channels]
    assert ch_ids == ["C001", "C002"]  # C003 filtered (not a member)
    general = next(c for c in result.channels if c.id == "C001")
    assert general.risk == "elevated"
    user_ids = [u.id for u in result.users]
    assert "B001" not in user_ids  # bot filtered
    alice = next(u for u in result.users if u.id == "U001")
    assert alice.risk == "danger"  # admin
