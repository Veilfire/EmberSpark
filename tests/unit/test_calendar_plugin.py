"""Calendar (CalDAV) plugin — schema, allowlist gates, error mapping."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from spark.errors.codes import ErrorCode, SparkError
from spark.plugins.builtins.calendar import (
    CalendarConfig,
    CalendarPlugin,
    _CalendarArgsWrapper,
    _classify_caldav_error,
    _parse_dt,
    _refuse_calendar,
    _refuse_read_only,
    discover,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ctx(secrets: dict[str, str], cfg: dict[str, object]):
    class _Ctx:
        pass

    ctx = _Ctx()
    ctx.secrets = secrets  # type: ignore[attr-defined]
    ctx.plugin_config = cfg  # type: ignore[attr-defined]
    return ctx


def _wrapper(action: str, **kw):
    return _CalendarArgsWrapper(action=action, **kw)


def _fake_principal(calendars=()):
    p = MagicMock()
    p.calendars = MagicMock(return_value=list(calendars))
    p.url = "https://caldav.example.com/principal/"
    return p


def _fake_caldav_client(principal):
    c = MagicMock()
    c.principal = MagicMock(return_value=principal)
    return c


def _fake_calendar(url: str, *, name: str = "", events=()):
    cal = MagicMock()
    cal.url = url
    cal.name = name
    cal.search = MagicMock(return_value=list(events))
    return cal


# ---------------------------------------------------------------------------
# Config + schema
# ---------------------------------------------------------------------------


def test_config_defaults():
    cfg = CalendarConfig()
    assert cfg.read_only is True
    assert cfg.password_secret == "calendar_password"
    assert cfg.allowed_calendars == []
    assert cfg.default_calendar == ""
    assert cfg.verify_ssl is True
    assert cfg.max_events_returned == 200


def test_parse_dt_iso():
    dt = _parse_dt("2026-05-09T10:00:00Z", "UTC")
    assert dt == datetime(2026, 5, 9, 10, 0, tzinfo=timezone.utc)


def test_parse_dt_date_only():
    dt = _parse_dt("2026-05-09", "UTC")
    assert dt == datetime(2026, 5, 9, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Refusal helpers map to SparkError shapes the Failure Inspector expects
# ---------------------------------------------------------------------------


def test_refuse_calendar_shape():
    err = _refuse_calendar("https://caldav.example.com/work/")
    assert err.code is ErrorCode.PERMISSION_MISSING
    assert err.detail["plugin"] == "calendar"
    assert err.detail["missing_allowlist_item"] == "https://caldav.example.com/work/"
    assert err.detail["field"] == "allowed_calendars"


def test_refuse_read_only_shape():
    err = _refuse_read_only()
    assert err.code is ErrorCode.PERMISSION_MISSING
    assert err.detail["plugin"] == "calendar"
    assert err.detail["missing_toggle"] == "read_only"


def test_refused_emits_failure_inspector_options():
    """The catalogue should produce a deep-link option for the plugin's
    refusal shape. End-to-end check that the generic
    `plugin_allowlist_grant` branch fires."""
    err = SparkError(
        ErrorCode.PERMISSION_MISSING,
        "calendar: refused",
        detail={
            "plugin": "calendar",
            "missing_allowlist_item": "https://caldav.example.com/work/",
            "field": "allowed_calendars",
        },
    )
    payload = err.to_dict()
    actionable = [t for t in payload["tuning"] if t["deep_link"]]
    assert actionable, "expected at least one deep-linkable option"
    first = actionable[0]
    assert first["prefill"]["kind"] == "plugin_allowlist_grant"
    assert first["prefill"]["plugin"] == "calendar"
    assert first["prefill"]["add_item"] == "https://caldav.example.com/work/"
    assert first["prefill"]["field"] == "allowed_calendars"


# ---------------------------------------------------------------------------
# Execute — refusal paths (don't need a live CalDAV server)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_refuses_when_base_url_empty():
    plugin = CalendarPlugin()
    ctx = _ctx(secrets={"calendar_password": "p"}, cfg={})
    with pytest.raises(SparkError) as exc:
        await plugin.execute(_wrapper("list_calendars"), ctx)
    assert exc.value.code is ErrorCode.OPERATOR_OVERRIDE_REFUSED
    assert exc.value.detail["field"] == "base_url"


@pytest.mark.asyncio
async def test_execute_refuses_when_secret_missing():
    plugin = CalendarPlugin()
    ctx = _ctx(
        secrets={},
        cfg={"base_url": "https://caldav.example.com", "username": "u"},
    )
    with pytest.raises(SparkError) as exc:
        await plugin.execute(_wrapper("list_calendars"), ctx)
    assert exc.value.code is ErrorCode.SECRET_NOT_FOUND
    assert exc.value.detail["plugin"] == "calendar"
    assert exc.value.detail["secret_name"] == "calendar_password"


@pytest.mark.asyncio
async def test_list_events_with_no_allowed_calendars_returns_empty():
    plugin = CalendarPlugin()
    ctx = _ctx(
        secrets={"calendar_password": "p"},
        cfg={
            "base_url": "https://caldav.example.com",
            "username": "u",
        },
    )
    # caldav client builds but principal()/calendars() are mocked.
    with patch("caldav.DAVClient") as DAVClient:
        DAVClient.return_value = _fake_caldav_client(_fake_principal())
        result = await plugin.execute(
            _wrapper(
                "list_events",
                start="2026-05-09",
                end="2026-05-10",
            ),
            ctx,
        )
    assert result.ok is True
    assert result.events == []


@pytest.mark.asyncio
async def test_list_events_for_unallowed_calendar_refuses():
    plugin = CalendarPlugin()
    ctx = _ctx(
        secrets={"calendar_password": "p"},
        cfg={
            "base_url": "https://caldav.example.com",
            "username": "u",
            "allowed_calendars": ["https://caldav.example.com/work/"],
        },
    )
    with patch("caldav.DAVClient") as DAVClient:
        DAVClient.return_value = _fake_caldav_client(_fake_principal())
        with pytest.raises(SparkError) as exc:
            await plugin.execute(
                _wrapper(
                    "list_events",
                    start="2026-05-09",
                    end="2026-05-10",
                    calendar_url="https://caldav.example.com/personal/",
                ),
                ctx,
            )
    assert exc.value.code is ErrorCode.PERMISSION_MISSING
    assert exc.value.detail["missing_allowlist_item"] == "https://caldav.example.com/personal/"
    assert exc.value.detail["field"] == "allowed_calendars"


@pytest.mark.asyncio
async def test_create_event_refused_when_read_only():
    plugin = CalendarPlugin()
    ctx = _ctx(
        secrets={"calendar_password": "p"},
        cfg={
            "base_url": "https://caldav.example.com",
            "username": "u",
            "read_only": True,
            "default_calendar": "https://caldav.example.com/work/",
            "allowed_calendars": ["https://caldav.example.com/work/"],
        },
    )
    with patch("caldav.DAVClient") as DAVClient:
        DAVClient.return_value = _fake_caldav_client(_fake_principal())
        with pytest.raises(SparkError) as exc:
            await plugin.execute(
                _wrapper(
                    "create_event",
                    title="Test",
                    start="2026-05-09T10:00:00Z",
                    end="2026-05-09T11:00:00Z",
                ),
                ctx,
            )
    assert exc.value.code is ErrorCode.PERMISSION_MISSING
    assert exc.value.detail["missing_toggle"] == "read_only"


@pytest.mark.asyncio
async def test_create_event_refused_when_default_calendar_unset():
    plugin = CalendarPlugin()
    ctx = _ctx(
        secrets={"calendar_password": "p"},
        cfg={
            "base_url": "https://caldav.example.com",
            "username": "u",
            "read_only": False,
            "default_calendar": "",
            "allowed_calendars": ["https://caldav.example.com/work/"],
        },
    )
    with patch("caldav.DAVClient") as DAVClient:
        DAVClient.return_value = _fake_caldav_client(_fake_principal())
        with pytest.raises(SparkError) as exc:
            await plugin.execute(
                _wrapper(
                    "create_event",
                    title="Test",
                    start="2026-05-09T10:00:00Z",
                    end="2026-05-09T11:00:00Z",
                ),
                ctx,
            )
    assert exc.value.code is ErrorCode.OPERATOR_OVERRIDE_REFUSED
    assert exc.value.detail["field"] == "default_calendar"


@pytest.mark.asyncio
async def test_get_history_requires_event_url():
    plugin = CalendarPlugin()
    ctx = _ctx(
        secrets={"calendar_password": "p"},
        cfg={"base_url": "https://caldav.example.com", "username": "u"},
    )
    with patch("caldav.DAVClient") as DAVClient:
        DAVClient.return_value = _fake_caldav_client(_fake_principal())
        with pytest.raises(SparkError) as exc:
            await plugin.execute(_wrapper("get_event"), ctx)
    assert exc.value.code is ErrorCode.INPUT_SCHEMA_INVALID


# ---------------------------------------------------------------------------
# classify_caldav_error
# ---------------------------------------------------------------------------


def test_classify_auth_error_maps_to_secret_not_found():
    import caldav.lib.error as caldav_errors

    exc = caldav_errors.AuthorizationError("401")
    err = _classify_caldav_error(exc, base_url="https://caldav.example.com")
    assert err.code is ErrorCode.SECRET_NOT_FOUND


def test_classify_connect_error_to_private_ip():
    exc = Exception("Connection refused")
    err = _classify_caldav_error(exc, base_url="http://192.168.1.10/")
    assert err.code is ErrorCode.URL_PRIVATE_IP
    assert err.detail["host"] == "192.168.1.10"


def test_classify_connect_error_to_public_host():
    exc = Exception("Connection timed out")
    err = _classify_caldav_error(exc, base_url="https://caldav.example.com")
    assert err.code is ErrorCode.URL_DENIED


# ---------------------------------------------------------------------------
# discover()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_discover_returns_error_when_token_missing():
    cfg = {"base_url": "https://caldav.example.com", "username": "u"}
    ctx = _ctx(secrets={}, cfg=cfg)
    result = await discover(cfg, ctx)
    assert result.ok is False
    assert result.error_code == ErrorCode.SECRET_NOT_FOUND.value


@pytest.mark.asyncio
async def test_discover_empty_base_url():
    result = await discover({}, _ctx({}, {}))
    assert result.ok is False
    assert result.error_code == ErrorCode.OPERATOR_OVERRIDE_REFUSED.value


@pytest.mark.asyncio
async def test_discover_happy_path():
    cfg = {
        "base_url": "https://caldav.example.com",
        "username": "u",
    }
    ctx = _ctx(secrets={"calendar_password": "p"}, cfg=cfg)
    calendars = [
        _fake_calendar("https://caldav.example.com/work/", name="Work"),
        _fake_calendar("https://caldav.example.com/personal/", name="Personal"),
    ]
    with patch("caldav.DAVClient") as DAVClient:
        DAVClient.return_value = _fake_caldav_client(_fake_principal(calendars))
        result = await discover(cfg, ctx)
    assert result.ok is True
    assert len(result.calendars) == 2
    names = [c.name for c in result.calendars]
    assert "Work" in names and "Personal" in names
    # All discovered calendars classified safe by default; danger
    # classification kicks in once we add ACL detection.
    assert all(c.risk == "safe" for c in result.calendars)
