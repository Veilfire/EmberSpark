"""Home Assistant plugin — schema, allowlists, error mapping, discover."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from spark.errors.codes import ErrorCode, SparkError
from spark.plugins.builtins.home_assistant import (
    HomeAssistantConfig,
    HomeAssistantPlugin,
    _DEFAULT_ALLOWED_DOMAINS,
    _HomeAssistantArgsWrapper,
    discover,
    domain_risk,
    service_risk,
)


# ---------------------------------------------------------------------------
# Helpers — mock httpx responses end-to-end
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, status_code: int, body: str):
        self.status_code = status_code
        self.text = body


def _make_ctx(secrets: dict[str, str], cfg: dict[str, Any]):
    class _Ctx:
        pass

    ctx = _Ctx()
    ctx.secrets = secrets  # type: ignore[attr-defined]
    ctx.plugin_config = cfg  # type: ignore[attr-defined]
    return ctx


def _patch_request(responses: dict[str, _FakeResponse]):
    """Patch ``httpx.AsyncClient.request`` with a URL → response map."""

    async def fake_request(self, method, url, *args, **kwargs):  # noqa: ANN001
        for needle, resp in responses.items():
            if needle in url:
                return resp
        return _FakeResponse(404, "{}")

    return patch.object(httpx.AsyncClient, "request", new=fake_request)


# ---------------------------------------------------------------------------
# Risk classification
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "domain,expected",
    [
        ("light", "safe"),
        ("switch", "safe"),
        ("sensor", "safe"),
        ("media_player", "elevated"),
        ("cover", "elevated"),
        ("script", "elevated"),
        ("automation", "elevated"),
        ("lock", "danger"),
        ("alarm_control_panel", "danger"),
        ("camera", "danger"),
        ("device_tracker", "danger"),
        ("person", "danger"),
        ("vacuum", "danger"),
        ("input_boolean", "safe"),
    ],
)
def test_domain_risk(domain, expected):
    assert domain_risk(domain) == expected


@pytest.mark.parametrize(
    "domain,service,expected",
    [
        ("light", "turn_on", "safe"),
        ("light", "turn_off", "safe"),
        ("light", "toggle", "elevated"),
        ("media_player", "play_media", "elevated"),
        ("media_player", "media_pause", "elevated"),
        ("lock", "unlock", "danger"),
        ("lock", "lock", "danger"),  # any service on a danger domain → danger
        ("alarm_control_panel", "disarm", "danger"),
        ("alarm_control_panel", "disarm_away", "danger"),
        ("script", "execute", "danger"),
        ("automation", "trigger", "danger"),
        ("homeassistant", "restart", "danger"),
        ("cover", "open_cover", "danger"),
        ("cover", "close_cover", "elevated"),
        ("scene", "turn_on", "elevated"),
    ],
)
def test_service_risk(domain, service, expected):
    assert service_risk(domain, service) == expected


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------


def test_default_allowed_domains_excludes_danger_set():
    danger = {"lock", "alarm_control_panel", "camera", "device_tracker", "person", "vacuum"}
    assert not danger & set(_DEFAULT_ALLOWED_DOMAINS)


def test_config_defaults():
    cfg = HomeAssistantConfig()
    assert cfg.read_only is True
    assert cfg.token_secret == "home_assistant_token"
    assert cfg.allowed_services == {}
    assert cfg.entity_filter_glob == []
    assert cfg.verify_ssl is True


# ---------------------------------------------------------------------------
# Action dispatch + execute
# ---------------------------------------------------------------------------


def _wrapper(action: str, **kw):
    return _HomeAssistantArgsWrapper(action=action, **kw)


@pytest.mark.asyncio
async def test_missing_token_raises_secret_not_found():
    plugin = HomeAssistantPlugin()
    ctx = _make_ctx(
        secrets={},
        cfg={"base_url": "http://ha.lan:8123"},
    )
    with pytest.raises(SparkError) as exc:
        await plugin.execute(_wrapper("list_states"), ctx)
    assert exc.value.code is ErrorCode.SECRET_NOT_FOUND
    assert exc.value.detail.get("plugin") == "home_assistant"
    assert exc.value.detail.get("secret_name") == "home_assistant_token"


@pytest.mark.asyncio
async def test_empty_base_url_raises():
    plugin = HomeAssistantPlugin()
    ctx = _make_ctx(secrets={"home_assistant_token": "t"}, cfg={})
    with pytest.raises(SparkError) as exc:
        await plugin.execute(_wrapper("list_states"), ctx)
    assert exc.value.code is ErrorCode.OPERATOR_OVERRIDE_REFUSED
    assert exc.value.detail.get("field") == "base_url"


@pytest.mark.asyncio
async def test_list_states_filters_disallowed_domains():
    plugin = HomeAssistantPlugin()
    body = json.dumps(
        [
            {"entity_id": "light.kitchen", "state": "on", "attributes": {"friendly_name": "Kitchen"}},
            {"entity_id": "device_tracker.phone", "state": "home", "attributes": {}},
            {"entity_id": "switch.living", "state": "off", "attributes": {}},
        ]
    )
    ctx = _make_ctx(
        secrets={"home_assistant_token": "tok"},
        cfg={"base_url": "http://ha.lan:8123"},
    )
    with _patch_request({"/api/states": _FakeResponse(200, body)}):
        result = await plugin.execute(_wrapper("list_states"), ctx)
    assert result.ok is True
    ids = [s.entity_id for s in (result.states or [])]
    assert "light.kitchen" in ids
    assert "switch.living" in ids
    # device_tracker excluded by default — not visible.
    assert "device_tracker.phone" not in ids


@pytest.mark.asyncio
async def test_get_state_refuses_disallowed_domain():
    plugin = HomeAssistantPlugin()
    ctx = _make_ctx(
        secrets={"home_assistant_token": "tok"},
        cfg={"base_url": "http://ha.lan:8123"},
    )
    with pytest.raises(SparkError) as exc:
        await plugin.execute(
            _wrapper("get_state", entity_id="device_tracker.phone"), ctx
        )
    assert exc.value.code is ErrorCode.PERMISSION_MISSING
    assert exc.value.detail["missing_domain"] == "device_tracker"


@pytest.mark.asyncio
async def test_call_service_refused_when_read_only():
    plugin = HomeAssistantPlugin()
    ctx = _make_ctx(
        secrets={"home_assistant_token": "tok"},
        cfg={
            "base_url": "http://ha.lan:8123",
            "read_only": True,
            "allowed_services": {"light": ["turn_off"]},
        },
    )
    with pytest.raises(SparkError) as exc:
        await plugin.execute(
            _wrapper("call_service", domain="light", service="turn_off"),
            ctx,
        )
    assert exc.value.code is ErrorCode.PERMISSION_MISSING
    assert exc.value.detail["missing_toggle"] == "read_only"


@pytest.mark.asyncio
async def test_call_service_refused_when_service_not_allowed():
    plugin = HomeAssistantPlugin()
    ctx = _make_ctx(
        secrets={"home_assistant_token": "tok"},
        cfg={
            "base_url": "http://ha.lan:8123",
            "read_only": False,
            "allowed_services": {"light": ["turn_on"]},  # turn_off not allowed
        },
    )
    with pytest.raises(SparkError) as exc:
        await plugin.execute(
            _wrapper("call_service", domain="light", service="turn_off"),
            ctx,
        )
    assert exc.value.code is ErrorCode.PERMISSION_MISSING
    assert exc.value.detail["missing_service"] == "light.turn_off"


@pytest.mark.asyncio
async def test_call_service_refused_when_domain_not_allowed():
    plugin = HomeAssistantPlugin()
    ctx = _make_ctx(
        secrets={"home_assistant_token": "tok"},
        cfg={
            "base_url": "http://ha.lan:8123",
            "read_only": False,
            "allowed_services": {"lock": ["unlock"]},
        },
    )
    # Domain `lock` isn't in allowed_domains by default, so even though
    # allowed_services has it, the domain gate refuses first.
    with pytest.raises(SparkError) as exc:
        await plugin.execute(
            _wrapper("call_service", domain="lock", service="unlock"),
            ctx,
        )
    assert exc.value.code is ErrorCode.PERMISSION_MISSING
    assert exc.value.detail["missing_domain"] == "lock"


@pytest.mark.asyncio
async def test_call_service_succeeds_with_proper_allowlists():
    plugin = HomeAssistantPlugin()
    ctx = _make_ctx(
        secrets={"home_assistant_token": "tok"},
        cfg={
            "base_url": "http://ha.lan:8123",
            "read_only": False,
            "allowed_services": {"light": ["turn_off"]},
        },
    )
    with _patch_request({"/api/services/light/turn_off": _FakeResponse(200, "[]")}):
        result = await plugin.execute(
            _wrapper("call_service", domain="light", service="turn_off", entity_id="light.kitchen"),
            ctx,
        )
    assert result.ok is True
    assert result.action == "call_service"


@pytest.mark.asyncio
async def test_get_history_requires_entity_id():
    plugin = HomeAssistantPlugin()
    ctx = _make_ctx(
        secrets={"home_assistant_token": "tok"},
        cfg={"base_url": "http://ha.lan:8123"},
    )
    with pytest.raises(SparkError) as exc:
        await plugin.execute(_wrapper("get_history"), ctx)
    assert exc.value.code is ErrorCode.INPUT_SCHEMA_INVALID


@pytest.mark.asyncio
async def test_401_from_ha_raises_secret_not_found():
    plugin = HomeAssistantPlugin()
    ctx = _make_ctx(
        secrets={"home_assistant_token": "bad"},
        cfg={"base_url": "http://ha.lan:8123"},
    )
    with _patch_request({"/api/states": _FakeResponse(401, "Unauthorized")}):
        with pytest.raises(SparkError) as exc:
            await plugin.execute(_wrapper("list_states"), ctx)
    assert exc.value.code is ErrorCode.SECRET_NOT_FOUND


@pytest.mark.asyncio
async def test_connect_to_private_ip_maps_to_url_private_ip():
    plugin = HomeAssistantPlugin()
    ctx = _make_ctx(
        secrets={"home_assistant_token": "tok"},
        cfg={"base_url": "http://192.168.1.10:8123"},
    )

    async def boom(self, *a, **kw):  # noqa: ANN001
        raise httpx.ConnectError("connection refused")

    with patch.object(httpx.AsyncClient, "request", new=boom):
        with pytest.raises(SparkError) as exc:
            await plugin.execute(_wrapper("list_states"), ctx)
    assert exc.value.code is ErrorCode.URL_PRIVATE_IP


@pytest.mark.asyncio
async def test_entity_filter_glob_excludes():
    plugin = HomeAssistantPlugin()
    body = json.dumps(
        [
            {"entity_id": "light.kitchen", "state": "on", "attributes": {}},
            {"entity_id": "light.bedroom", "state": "off", "attributes": {}},
        ]
    )
    ctx = _make_ctx(
        secrets={"home_assistant_token": "tok"},
        cfg={
            "base_url": "http://ha.lan:8123",
            "entity_filter_glob": ["light.bedroom"],
        },
    )
    with _patch_request({"/api/states": _FakeResponse(200, body)}):
        result = await plugin.execute(_wrapper("list_states"), ctx)
    ids = [s.entity_id for s in (result.states or [])]
    assert "light.kitchen" in ids
    assert "light.bedroom" not in ids


@pytest.mark.asyncio
async def test_max_states_returned_truncates():
    plugin = HomeAssistantPlugin()
    rows = [
        {"entity_id": f"light.l{i}", "state": "on", "attributes": {}}
        for i in range(50)
    ]
    body = json.dumps(rows)
    ctx = _make_ctx(
        secrets={"home_assistant_token": "tok"},
        cfg={
            "base_url": "http://ha.lan:8123",
            "max_states_returned": 10,
        },
    )
    with _patch_request({"/api/states": _FakeResponse(200, body)}):
        result = await plugin.execute(_wrapper("list_states"), ctx)
    assert len(result.states or []) == 10
    assert result.truncated is True


# ---------------------------------------------------------------------------
# discover()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_discover_returns_error_when_token_missing():
    cfg = {"base_url": "http://ha.lan:8123", "token_secret": "missing_secret"}
    ctx = _make_ctx(secrets={}, cfg=cfg)
    result = await discover(cfg, ctx)
    assert result.ok is False
    assert result.error_code == ErrorCode.SECRET_NOT_FOUND.value


@pytest.mark.asyncio
async def test_discover_happy_path():
    cfg = {"base_url": "http://ha.lan:8123"}
    ctx = _make_ctx(secrets={"home_assistant_token": "tok"}, cfg=cfg)
    config_body = json.dumps({"version": "2026.5.1", "location_name": "Home"})
    services_body = json.dumps(
        [
            {"domain": "light", "services": {"turn_on": {"description": "Turn on"}, "turn_off": {}, "toggle": {}}},
            {"domain": "lock", "services": {"unlock": {}, "lock": {}}},
        ]
    )
    states_body = json.dumps(
        [
            {"entity_id": "light.kitchen", "state": "on", "attributes": {"friendly_name": "Kitchen"}},
            {"entity_id": "lock.front", "state": "locked", "attributes": {}},
        ]
    )
    with _patch_request(
        {
            "/api/config": _FakeResponse(200, config_body),
            "/api/services": _FakeResponse(200, services_body),
            "/api/states": _FakeResponse(200, states_body),
        }
    ):
        result = await discover(cfg, ctx)
    assert result.ok is True
    assert result.instance_version == "2026.5.1"
    domain_names = [d.name for d in result.domains]
    assert "light" in domain_names
    assert "lock" in domain_names
    # Risk classification surfaces.
    lock_entry = next(d for d in result.domains if d.name == "lock")
    assert lock_entry.risk == "danger"
    light_entry = next(d for d in result.domains if d.name == "light")
    assert light_entry.risk == "safe"
    assert light_entry.entity_count == 1
    # Services-by-domain populated + risk-classified.
    light_services = {s.name: s.risk for s in result.services_by_domain["light"]}
    assert light_services["turn_on"] == "safe"
    assert light_services["toggle"] == "elevated"
    lock_services = {s.name: s.risk for s in result.services_by_domain["lock"]}
    assert lock_services["unlock"] == "danger"


@pytest.mark.asyncio
async def test_discover_returns_error_when_base_url_blank():
    result = await discover({}, _make_ctx({}, {}))
    assert result.ok is False
    assert result.error_code == ErrorCode.OPERATOR_OVERRIDE_REFUSED.value
