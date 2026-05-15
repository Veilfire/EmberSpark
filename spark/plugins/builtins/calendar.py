"""Calendar plugin — CalDAV reader + writer.

CalDAV is the standard protocol for calendar access; iCloud, Google,
Outlook, Nextcloud, FastMail, mailbox.org all speak it. Single plugin
covers every realistic operator.

Library: `caldav` (mature, MIT-licensed, mature DAV client). It's
sync-only (built on `requests`), so we wrap calls in
``asyncio.to_thread`` to keep the plugin's ``execute`` async without
blocking the event loop. That's the same shape we use for any
sync-only lib (e.g. the existing `pypdf`-using pdf_reader).

Five guard rails:

1. **Read-only by default** — ``read_only=true`` refuses every write
   path (``create_event`` / ``update_event`` / ``delete_event``).
2. **Calendar allowlist** — even reads require the calendar's URL to
   be in ``allowed_calendars``. Default empty = refuse all.
3. **`default_calendar` for writes** — when ``read_only=false``, the
   plugin writes only into the named default calendar (which must be
   in ``allowed_calendars``).
4. **Sensitivity = MODERATE** — titles / locations / attendees flow
   through Presidio for `LOCATION` / `EMAIL_ADDRESS` / `PERSON`
   redaction before reaching the model.
5. **SparkError raises** map to stable codes — `SECRET_NOT_FOUND` /
   `PERMISSION_MISSING` / `URL_PRIVATE_IP` / `URL_DENIED` — so the
   Failure Inspector catalogue produces the right tuning options
   without per-plugin frontend code.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, ClassVar, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field

from spark.config.enums import Permission, Sensitivity
from spark.errors import ErrorCode, SparkError


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


class CalendarConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_url: str = Field(
        default="",
        max_length=512,
        description=(
            "CalDAV server URL — e.g. "
            "https://caldav.icloud.com, "
            "https://www.google.com/calendar/dav/, "
            "https://outlook.office365.com/owa/, "
            "https://nextcloud.example.com/remote.php/dav/"
        ),
    )
    username: str = Field(
        default="",
        max_length=256,
        description="Account username (often the email address).",
    )
    password_secret: str = Field(
        default="calendar_password",
        max_length=128,
        description=(
            "Vault key for the app password. Never the iCloud login "
            "password — generate an app-specific one at "
            "appleid.apple.com (for iCloud) or app-passwords (Google "
            "with 2FA on, Outlook, etc.)."
        ),
    )
    read_only: bool = Field(default=True)
    allowed_calendars: list[str] = Field(
        default_factory=list,
        description=(
            "Calendar URL paths the agent can read / write. Discover "
            "lists every calendar; operator ticks the allowed ones."
        ),
    )
    default_calendar: str = Field(
        default="",
        max_length=512,
        description=(
            "Which calendar create_event writes to. Must be in "
            "allowed_calendars. Empty refuses creation even when "
            "read_only=false."
        ),
    )
    verify_ssl: bool = Field(default=True)
    timezone_default: str = Field(
        default="UTC",
        max_length=64,
        description="Fallback timezone for naive datetime inputs.",
    )
    max_events_returned: int = Field(default=200, gt=0, le=2000)
    connect_timeout_seconds: float = Field(default=10.0, gt=0, le=60)
    read_timeout_seconds: float = Field(default=30.0, gt=0, le=120)


# ---------------------------------------------------------------------------
# Action surface
# ---------------------------------------------------------------------------


class _ListCalendarsArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["list_calendars"] = "list_calendars"


class _ListEventsArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["list_events"] = "list_events"
    start: str = Field(
        description=(
            "ISO-8601 timestamp / date (inclusive lower bound). "
            "Examples: '2026-05-09T00:00:00Z', '2026-05-09'."
        )
    )
    end: str = Field(description="ISO-8601 timestamp / date (exclusive upper bound).")
    calendar_url: str | None = Field(
        default=None,
        description=(
            "Optional: only this calendar (must be in allowed_calendars). "
            "Defaults to every allowed calendar."
        ),
    )


class _GetEventArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["get_event"] = "get_event"
    event_url: str = Field(min_length=1, max_length=1024)


class _CreateEventArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["create_event"] = "create_event"
    title: str = Field(min_length=1, max_length=512)
    start: str
    end: str
    description: str | None = Field(default=None, max_length=2048)
    location: str | None = Field(default=None, max_length=512)
    attendees: list[str] = Field(default_factory=list, max_length=50)
    all_day: bool = False


class _DeleteEventArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["delete_event"] = "delete_event"
    event_url: str = Field(min_length=1, max_length=1024)


class _FindFreeSlotsArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["find_free_slots"] = "find_free_slots"
    window_start: str
    window_end: str
    duration_minutes: int = Field(gt=0, le=720)


class _CalendarArgsWrapper(BaseModel):
    """Discriminated-union dispatch on ``action``.

    Permissive top-level shape mirroring the telegram pattern —
    inner `_*Args` does the per-action validation in ``execute``.
    """

    model_config = ConfigDict(extra="forbid")
    action: Literal[
        "list_calendars",
        "list_events",
        "get_event",
        "create_event",
        "delete_event",
        "find_free_slots",
    ] = Field(
        description=(
            "Which calendar operation to run. 'list_calendars' "
            "(discovery), 'list_events' (range query), 'get_event' "
            "(by URL), 'create_event' (write — refused while "
            "read_only), 'delete_event' (write), 'find_free_slots' "
            "(find gaps in a window)."
        ),
    )
    start: str | None = None
    end: str | None = None
    window_start: str | None = None
    window_end: str | None = None
    duration_minutes: int | None = None
    calendar_url: str | None = None
    event_url: str | None = None
    title: str | None = None
    description: str | None = None
    location: str | None = None
    attendees: list[str] | None = None
    all_day: bool | None = None


class CalendarEvent(BaseModel):
    """Trimmed event record returned by list/get operations."""

    model_config = ConfigDict(extra="forbid")
    url: str
    calendar_url: str
    uid: str | None = None
    summary: str | None = None
    description: str | None = None
    location: str | None = None
    start: str | None = None
    end: str | None = None
    all_day: bool = False
    attendees: list[str] = Field(default_factory=list)
    status: str | None = None


class CalendarSlot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start: str
    end: str
    duration_minutes: int


class CalendarResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: str
    ok: bool
    calendars: list[dict[str, Any]] | None = None
    events: list[CalendarEvent] | None = None
    event: CalendarEvent | None = None
    slots: list[CalendarSlot] | None = None
    created_url: str | None = None
    truncated: bool = False
    error: str | None = None


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


class CalendarDiscoveryEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    url: str
    color: str | None = None
    can_write: bool = True
    risk: Literal["safe", "elevated", "danger"] = "safe"


class CalendarDiscovery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ok: bool
    error: str | None = None
    error_code: str | None = None
    error_detail: dict[str, Any] | None = None
    principal_url: str | None = None
    instance_name: str | None = None
    calendars: list[CalendarDiscoveryEntry] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _looks_private(host: str) -> bool:
    """Mirror of the shared helper — kept inline so we can map the
    sync caldav exception to ``URL_PRIVATE_IP`` without importing the
    httpx-shaped base module here."""
    import ipaddress  # noqa: PLC0415

    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_unspecified
    )


def _hostname(url: str) -> str:
    return (urlparse(url).hostname or "").strip()


def _resolve_password(cfg: dict[str, Any], ctx: Any) -> str:
    secret_name = (cfg.get("password_secret") or "calendar_password").strip()
    secrets = getattr(ctx, "secrets", {}) or {}
    value = secrets.get(secret_name) if isinstance(secrets, dict) else None
    if not value:
        raise SparkError(
            ErrorCode.SECRET_NOT_FOUND,
            f"calendar: secret {secret_name!r} not injected",
            detail={"plugin": "calendar", "secret_name": secret_name},
        )
    return str(value)


def _parse_dt(value: str, default_tz: str) -> datetime:
    """Parse ISO-8601 datetime or date. Falls back to default_tz when naive."""
    try:
        from zoneinfo import ZoneInfo  # noqa: PLC0415

        tz = ZoneInfo(default_tz)
    except Exception:
        tz = timezone.utc

    s = value.strip()
    # Date-only string
    if len(s) == 10 and s[4] == "-" and s[7] == "-":
        dt = datetime.fromisoformat(s + "T00:00:00")
        return dt.replace(tzinfo=tz)
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz)
    return dt


def _trim_event(event: Any) -> CalendarEvent:
    """Map a ``caldav.Event`` to our trimmed view.

    Defensive: caldav's vobject parsing can return surprising shapes
    on unusual events; we extract what we recognize and ignore the
    rest.
    """
    try:
        cal = event.icalendar_component  # icalendar.Event
    except Exception:
        cal = None
    url = getattr(event, "url", None) or ""
    cal_url = getattr(event, "parent", None)
    cal_url_str = str(getattr(cal_url, "url", "") or "")
    uid = str(cal.get("UID")) if cal and cal.get("UID") else None
    summary = str(cal.get("SUMMARY")) if cal and cal.get("SUMMARY") else None
    description = (
        str(cal.get("DESCRIPTION")) if cal and cal.get("DESCRIPTION") else None
    )
    location = str(cal.get("LOCATION")) if cal and cal.get("LOCATION") else None
    status = str(cal.get("STATUS")) if cal and cal.get("STATUS") else None

    start = None
    end = None
    all_day = False
    if cal:
        dtstart = cal.get("DTSTART")
        dtend = cal.get("DTEND")
        if dtstart is not None:
            v = getattr(dtstart, "dt", dtstart)
            if isinstance(v, datetime):
                start = v.isoformat()
            else:
                start = str(v)
                all_day = True
        if dtend is not None:
            v = getattr(dtend, "dt", dtend)
            if isinstance(v, datetime):
                end = v.isoformat()
            else:
                end = str(v)
                all_day = True

    attendees: list[str] = []
    if cal:
        att = cal.get("ATTENDEE", [])
        if not isinstance(att, list):
            att = [att]
        for a in att:
            s = str(a)
            if s.upper().startswith("MAILTO:"):
                s = s[7:]
            attendees.append(s)

    return CalendarEvent(
        url=str(url),
        calendar_url=cal_url_str,
        uid=uid,
        summary=summary,
        description=description,
        location=location,
        start=start,
        end=end,
        all_day=all_day,
        attendees=attendees,
        status=status,
    )


def _classify_caldav_error(exc: Exception, *, base_url: str) -> SparkError:
    """Map a caldav exception to a SparkError with stable code."""
    import caldav.lib.error as caldav_errors  # noqa: PLC0415

    host = _hostname(base_url)
    if isinstance(exc, caldav_errors.AuthorizationError):
        return SparkError(
            ErrorCode.SECRET_NOT_FOUND,
            f"calendar: auth refused by {host or base_url}",
            detail={
                "plugin": "calendar",
                "secret_name": "calendar_password",
            },
        )
    msg = str(exc)
    if "Connection" in msg or "connect" in msg.lower() or "refused" in msg.lower():
        code = (
            ErrorCode.URL_PRIVATE_IP
            if _looks_private(host)
            else ErrorCode.URL_DENIED
        )
        return SparkError(
            code,
            f"calendar: cannot reach {host or base_url}: {exc}",
            detail={"plugin": "calendar", "host": host},
        )
    return SparkError(
        ErrorCode.PLUGIN_RAISED,
        f"calendar: caldav error: {exc}",
        detail={"plugin": "calendar"},
    )


def _refuse_calendar(url: str) -> SparkError:
    return SparkError(
        ErrorCode.PERMISSION_MISSING,
        f"calendar: {url!r} not in allowed_calendars",
        detail={
            "plugin": "calendar",
            "missing_allowlist_item": url,
            "field": "allowed_calendars",
        },
    )


def _refuse_read_only() -> SparkError:
    return SparkError(
        ErrorCode.PERMISSION_MISSING,
        "calendar: read_only=true blocks write",
        detail={"plugin": "calendar", "missing_toggle": "read_only"},
    )


# ---------------------------------------------------------------------------
# Plugin
# ---------------------------------------------------------------------------


class CalendarPlugin:
    name: ClassVar[str] = "calendar"
    version: ClassVar[str] = "0.1.0"
    description: ClassVar[str] = (
        "View and (opt-in) write calendar events over CalDAV. Speaks "
        "to iCloud, Google, Outlook, Nextcloud, FastMail, etc."
    )
    input_schema: ClassVar[type[BaseModel]] = _CalendarArgsWrapper
    output_schema: ClassVar[type[BaseModel]] = CalendarResult
    config_schema: ClassVar[type[BaseModel]] = CalendarConfig
    required_permissions: ClassVar[frozenset[Permission]] = frozenset(
        {Permission.NET_HTTP, Permission.SECRETS_READ}
    )
    required_secrets: ClassVar[frozenset[str]] = frozenset()
    sensitivity: ClassVar[Sensitivity] = Sensitivity.MODERATE
    filter_output_before_model: ClassVar[bool] = True
    needs_network: ClassVar[bool] = True

    async def execute(
        self, args: _CalendarArgsWrapper, ctx: Any
    ) -> CalendarResult:
        cfg = getattr(ctx, "plugin_config", {}) or {}
        base_url = (cfg.get("base_url") or "").strip()
        if not base_url:
            raise SparkError(
                ErrorCode.OPERATOR_OVERRIDE_REFUSED,
                "calendar: base_url not set in operator config",
                detail={"plugin": "calendar", "field": "base_url"},
            )
        return await asyncio.to_thread(_execute_sync, args, cfg, ctx)


def _execute_sync(
    args: _CalendarArgsWrapper, cfg: dict[str, Any], ctx: Any
) -> CalendarResult:
    """Sync core dispatched via asyncio.to_thread. caldav is sync-only."""
    import caldav  # noqa: PLC0415

    base_url = (cfg.get("base_url") or "").strip()
    username = (cfg.get("username") or "").strip()
    password = _resolve_password(cfg, ctx)
    allowed_calendars = set(cfg.get("allowed_calendars") or [])
    default_calendar = (cfg.get("default_calendar") or "").strip()
    read_only = bool(cfg.get("read_only", True))
    tz_default = cfg.get("timezone_default") or "UTC"
    max_events = int(cfg.get("max_events_returned") or 200)
    verify = bool(cfg.get("verify_ssl", True))
    timeout = float(cfg.get("read_timeout_seconds") or 30.0)

    try:
        client = caldav.DAVClient(
            url=base_url,
            username=username,
            password=password,
            timeout=timeout,
            ssl_verify_cert=verify,
        )
    except Exception as exc:  # pragma: no cover — caldav init is forgiving
        raise _classify_caldav_error(exc, base_url=base_url) from exc

    try:
        if args.action == "list_calendars":
            return _do_list_calendars(client)
        if args.action == "list_events":
            return _do_list_events(
                args, client, allowed_calendars, max_events, tz_default
            )
        if args.action == "get_event":
            return _do_get_event(args, client, allowed_calendars)
        if args.action == "create_event":
            return _do_create_event(
                args, client, allowed_calendars,
                default_calendar=default_calendar,
                read_only=read_only,
                tz_default=tz_default,
            )
        if args.action == "delete_event":
            return _do_delete_event(args, client, allowed_calendars, read_only)
        if args.action == "find_free_slots":
            return _do_find_free_slots(
                args, client, allowed_calendars, tz_default
            )
        raise SparkError(
            ErrorCode.INPUT_SCHEMA_INVALID,
            f"calendar: unknown action {args.action!r}",
            detail={"plugin": "calendar", "action": args.action},
        )
    except SparkError:
        raise
    except Exception as exc:
        raise _classify_caldav_error(exc, base_url=base_url) from exc


def _do_list_calendars(client: Any) -> CalendarResult:
    principal = client.principal()
    out: list[dict[str, Any]] = []
    for cal in principal.calendars():
        out.append(
            {
                "name": str(getattr(cal, "name", None) or "") or _hostname(str(cal.url)),
                "url": str(cal.url),
                "color": None,
                "can_write": True,
            }
        )
    return CalendarResult(action="list_calendars", ok=True, calendars=out)


def _do_list_events(
    args: _CalendarArgsWrapper,
    client: Any,
    allowed: set[str],
    max_events: int,
    tz_default: str,
) -> CalendarResult:
    if not args.start or not args.end:
        raise SparkError(
            ErrorCode.INPUT_SCHEMA_INVALID,
            "calendar: list_events requires start and end",
            detail={"plugin": "calendar"},
        )
    start = _parse_dt(args.start, tz_default)
    end = _parse_dt(args.end, tz_default)

    target = args.calendar_url
    if target and target not in allowed:
        raise _refuse_calendar(target)
    if not allowed:
        return CalendarResult(action="list_events", ok=True, events=[])

    principal = client.principal()
    out: list[CalendarEvent] = []
    truncated = False
    for cal in principal.calendars():
        url_str = str(cal.url)
        if url_str not in allowed:
            continue
        if target and url_str != target:
            continue
        events = cal.search(start=start, end=end, event=True, expand=True)
        for ev in events:
            out.append(_trim_event(ev))
            if len(out) >= max_events:
                truncated = True
                break
        if truncated:
            break
    return CalendarResult(
        action="list_events", ok=True, events=out, truncated=truncated
    )


def _do_get_event(
    args: _CalendarArgsWrapper, client: Any, allowed: set[str]
) -> CalendarResult:
    if not args.event_url:
        raise SparkError(
            ErrorCode.INPUT_SCHEMA_INVALID,
            "calendar: get_event requires event_url",
            detail={"plugin": "calendar"},
        )
    principal = client.principal()
    for cal in principal.calendars():
        url_str = str(cal.url)
        if url_str not in allowed:
            continue
        try:
            ev = cal.event_by_url(args.event_url)
            return CalendarResult(
                action="get_event", ok=True, event=_trim_event(ev)
            )
        except Exception:
            continue
    return CalendarResult(
        action="get_event", ok=False, error="event not found in allowed calendars"
    )


def _do_create_event(
    args: _CalendarArgsWrapper,
    client: Any,
    allowed: set[str],
    *,
    default_calendar: str,
    read_only: bool,
    tz_default: str,
) -> CalendarResult:
    if read_only:
        raise _refuse_read_only()
    if not default_calendar:
        raise SparkError(
            ErrorCode.OPERATOR_OVERRIDE_REFUSED,
            "calendar: default_calendar not set; can't pick a target",
            detail={"plugin": "calendar", "field": "default_calendar"},
        )
    if default_calendar not in allowed:
        raise _refuse_calendar(default_calendar)
    if not args.title or not args.start or not args.end:
        raise SparkError(
            ErrorCode.INPUT_SCHEMA_INVALID,
            "calendar: create_event requires title, start, end",
            detail={"plugin": "calendar"},
        )

    import icalendar  # noqa: PLC0415
    from uuid import uuid4  # noqa: PLC0415

    cal = icalendar.Calendar()
    cal.add("prodid", "-//Spark//calendar plugin//EN")
    cal.add("version", "2.0")
    ev = icalendar.Event()
    uid = f"{uuid4()}@spark"
    ev.add("uid", uid)
    ev.add("summary", args.title)
    if args.description:
        ev.add("description", args.description)
    if args.location:
        ev.add("location", args.location)
    ev.add("dtstart", _parse_dt(args.start, tz_default))
    ev.add("dtend", _parse_dt(args.end, tz_default))
    for a in args.attendees or []:
        ev.add("attendee", f"mailto:{a}")
    ev.add("dtstamp", datetime.now(timezone.utc))
    cal.add_component(ev)
    ics = cal.to_ical().decode("utf-8")

    principal = client.principal()
    for c in principal.calendars():
        if str(c.url) == default_calendar:
            created = c.save_event(ics)
            return CalendarResult(
                action="create_event",
                ok=True,
                created_url=str(getattr(created, "url", "")),
            )
    raise _refuse_calendar(default_calendar)


def _do_delete_event(
    args: _CalendarArgsWrapper,
    client: Any,
    allowed: set[str],
    read_only: bool,
) -> CalendarResult:
    if read_only:
        raise _refuse_read_only()
    if not args.event_url:
        raise SparkError(
            ErrorCode.INPUT_SCHEMA_INVALID,
            "calendar: delete_event requires event_url",
            detail={"plugin": "calendar"},
        )
    principal = client.principal()
    for cal in principal.calendars():
        if str(cal.url) not in allowed:
            continue
        try:
            ev = cal.event_by_url(args.event_url)
            ev.delete()
            return CalendarResult(action="delete_event", ok=True)
        except Exception:
            continue
    return CalendarResult(
        action="delete_event", ok=False, error="event not found in allowed calendars"
    )


def _do_find_free_slots(
    args: _CalendarArgsWrapper,
    client: Any,
    allowed: set[str],
    tz_default: str,
) -> CalendarResult:
    if not args.window_start or not args.window_end or not args.duration_minutes:
        raise SparkError(
            ErrorCode.INPUT_SCHEMA_INVALID,
            "calendar: find_free_slots requires window_start, window_end, duration_minutes",
            detail={"plugin": "calendar"},
        )
    win_start = _parse_dt(args.window_start, tz_default)
    win_end = _parse_dt(args.window_end, tz_default)
    duration = timedelta(minutes=args.duration_minutes)
    if duration <= timedelta(0) or win_end <= win_start:
        return CalendarResult(action="find_free_slots", ok=True, slots=[])

    principal = client.principal()
    busy: list[tuple[datetime, datetime]] = []
    for cal in principal.calendars():
        if str(cal.url) not in allowed:
            continue
        try:
            events = cal.search(start=win_start, end=win_end, event=True, expand=True)
        except Exception:
            continue
        for ev in events:
            try:
                comp = ev.icalendar_component
                dtstart = comp.get("DTSTART")
                dtend = comp.get("DTEND")
                if dtstart is None or dtend is None:
                    continue
                s = getattr(dtstart, "dt", None)
                e = getattr(dtend, "dt", None)
                if isinstance(s, datetime) and isinstance(e, datetime):
                    busy.append((s, e))
            except Exception:
                continue

    busy.sort()
    slots: list[CalendarSlot] = []
    cursor = win_start
    for b_start, b_end in busy:
        if b_end <= cursor:
            continue
        if b_start > cursor:
            gap = b_start - cursor
            if gap >= duration:
                slots.append(
                    CalendarSlot(
                        start=cursor.isoformat(),
                        end=b_start.isoformat(),
                        duration_minutes=int(gap.total_seconds() // 60),
                    )
                )
        cursor = max(cursor, b_end)
    if cursor < win_end:
        gap = win_end - cursor
        if gap >= duration:
            slots.append(
                CalendarSlot(
                    start=cursor.isoformat(),
                    end=win_end.isoformat(),
                    duration_minutes=int(gap.total_seconds() // 60),
                )
            )
    return CalendarResult(action="find_free_slots", ok=True, slots=slots)


# ---------------------------------------------------------------------------
# Discovery — used by /api/plugin-config/calendar/discover
# ---------------------------------------------------------------------------


async def discover(cfg: dict[str, Any], ctx: Any) -> CalendarDiscovery:
    """Read-only CalDAV introspection for the Plugins-page editor."""
    base_url = (cfg.get("base_url") or "").strip()
    if not base_url:
        return CalendarDiscovery(
            ok=False,
            error="base_url not set",
            error_code=ErrorCode.OPERATOR_OVERRIDE_REFUSED.value,
            error_detail={"plugin": "calendar", "field": "base_url"},
        )
    try:
        password = _resolve_password(cfg, ctx)
    except SparkError as exc:
        return CalendarDiscovery(
            ok=False,
            error=exc.message,
            error_code=exc.code.value,
            error_detail=exc.detail,
        )
    return await asyncio.to_thread(_discover_sync, cfg, password)


def _discover_sync(cfg: dict[str, Any], password: str) -> CalendarDiscovery:
    import caldav  # noqa: PLC0415

    base_url = cfg["base_url"].strip()
    username = (cfg.get("username") or "").strip()
    verify = bool(cfg.get("verify_ssl", True))
    timeout = float(cfg.get("read_timeout_seconds") or 30.0)

    try:
        client = caldav.DAVClient(
            url=base_url,
            username=username,
            password=password,
            timeout=timeout,
            ssl_verify_cert=verify,
        )
        principal = client.principal()
        calendars = principal.calendars()
    except Exception as exc:
        err = _classify_caldav_error(exc, base_url=base_url)
        return CalendarDiscovery(
            ok=False,
            error=err.message,
            error_code=err.code.value,
            error_detail=err.detail,
        )

    out: list[CalendarDiscoveryEntry] = []
    for cal in calendars:
        out.append(
            CalendarDiscoveryEntry(
                name=str(getattr(cal, "name", None) or "")
                or _hostname(str(cal.url)),
                url=str(cal.url),
                color=None,
                can_write=True,
                risk="safe",
            )
        )
    return CalendarDiscovery(
        ok=True,
        principal_url=str(principal.url) if hasattr(principal, "url") else None,
        instance_name=_hostname(base_url),
        calendars=out,
    )
