"""Datetime utility plugin.

Zero-sensitivity, zero-network, zero-filesystem date/time helpers. The
sandbox policy for this plugin has no bind mounts and no network — it's
the strictest runtime config possible, which is safe because the plugin
does nothing except arithmetic on strings.

Operations:

- ``now``         — current time in a target timezone
- ``parse``       — ISO 8601 or RFC 2822 string → canonical ISO + epoch
- ``add``         — add a duration (days, hours, minutes, seconds) to an ISO
- ``diff``        — absolute difference between two ISO times in seconds
- ``to_timezone`` — convert an ISO string from one timezone to another
- ``is_dst``      — report whether a given time is in daylight saving

All outputs are ISO 8601 strings plus a POSIX epoch float so the agent
can do its own arithmetic without reimplementing timezone rules.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from typing import Any, ClassVar, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field

from spark.config.enums import Permission, Sensitivity


class DatetimeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    default_timezone: str = Field(default="UTC", max_length=64)
    allow_arbitrary_timezones: bool = True
    allowed_timezones: list[str] = Field(
        default_factory=list,
        description=(
            "If allow_arbitrary_timezones is False, only these IANA names "
            "are accepted."
        ),
    )


Op = Literal["now", "parse", "add", "diff", "to_timezone", "is_dst"]


class DatetimeArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    op: Op = Field(
        description=(
            "Operation: 'now' (current time), 'parse' (ISO-8601 → tz-aware), "
            "'add' (input + delta), 'diff' (input - other), "
            "'to_timezone' (re-zone input), 'is_dst' (boolean)."
        ),
    )
    timezone_name: str | None = Field(
        default=None,
        max_length=64,
        description="IANA timezone (e.g. 'America/New_York', 'UTC'). Required for 'now', 'to_timezone', 'is_dst'.",
    )
    input: str | None = Field(
        default=None,
        max_length=128,
        description="ISO-8601 timestamp (with timezone) for 'parse', 'add', 'diff', 'to_timezone', 'is_dst'.",
    )
    other: str | None = Field(
        default=None,
        max_length=128,
        description="Second ISO-8601 timestamp for 'diff'. Returns 'input - other' in seconds.",
    )
    days: int = Field(default=0, description="Days delta for 'add'.")
    hours: int = Field(default=0, description="Hours delta for 'add'.")
    minutes: int = Field(default=0, description="Minutes delta for 'add'.")
    seconds: int = Field(default=0, description="Seconds delta for 'add'.")


class DatetimeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    op: Op
    iso_string: str | None = None
    epoch_seconds: float | None = None
    timezone_name: str | None = None
    difference_seconds: float | None = None
    is_dst: bool | None = None


class DatetimePlugin:
    name: ClassVar[str] = "datetime"
    version: ClassVar[str] = "0.1.0"
    description: ClassVar[str] = (
        "Time utilities: now, parse, arithmetic, timezone conversion. "
        "No network, no filesystem, no secrets."
    )
    input_schema: ClassVar[type[BaseModel]] = DatetimeArgs
    output_schema: ClassVar[type[BaseModel]] = DatetimeResponse
    config_schema: ClassVar[type[BaseModel]] = DatetimeConfig
    required_permissions: ClassVar[frozenset[Permission]] = frozenset()
    required_secrets: ClassVar[frozenset[str]] = frozenset()
    sensitivity: ClassVar[Sensitivity] = Sensitivity.LOW
    filter_output_before_model: ClassVar[bool] = True
    needs_network: ClassVar[bool] = False

    async def execute(self, args: DatetimeArgs, ctx: Any) -> DatetimeResponse:
        cfg = getattr(ctx, "plugin_config", {}) or {}
        default_tz = cfg.get("default_timezone") or "UTC"
        allow_arbitrary = bool(cfg.get("allow_arbitrary_timezones", True))
        allowed_tzs = set(cfg.get("allowed_timezones") or [])

        tz_name = args.timezone_name or default_tz
        tz = _resolve_timezone(tz_name, allow_arbitrary=allow_arbitrary, allowlist=allowed_tzs)

        if args.op == "now":
            now = datetime.now(tz)
            return DatetimeResponse(
                op="now",
                iso_string=now.isoformat(),
                epoch_seconds=now.timestamp(),
                timezone_name=tz_name,
                is_dst=bool(now.dst()),
            )

        if args.op == "parse":
            if not args.input:
                raise PermissionError("datetime.parse requires `input`")
            parsed = _parse_iso_or_rfc(args.input)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=tz)
            return DatetimeResponse(
                op="parse",
                iso_string=parsed.isoformat(),
                epoch_seconds=parsed.timestamp(),
                timezone_name=str(parsed.tzinfo) if parsed.tzinfo else tz_name,
            )

        if args.op == "add":
            if not args.input:
                raise PermissionError("datetime.add requires `input`")
            base = _parse_iso_or_rfc(args.input)
            if base.tzinfo is None:
                base = base.replace(tzinfo=tz)
            delta = timedelta(
                days=args.days, hours=args.hours, minutes=args.minutes, seconds=args.seconds
            )
            result = base + delta
            return DatetimeResponse(
                op="add",
                iso_string=result.isoformat(),
                epoch_seconds=result.timestamp(),
                timezone_name=str(result.tzinfo) if result.tzinfo else tz_name,
            )

        if args.op == "diff":
            if not args.input or not args.other:
                raise PermissionError("datetime.diff requires `input` and `other`")
            a = _parse_iso_or_rfc(args.input)
            b = _parse_iso_or_rfc(args.other)
            if a.tzinfo is None:
                a = a.replace(tzinfo=tz)
            if b.tzinfo is None:
                b = b.replace(tzinfo=tz)
            delta = abs((a - b).total_seconds())
            return DatetimeResponse(op="diff", difference_seconds=delta)

        if args.op == "to_timezone":
            if not args.input:
                raise PermissionError("datetime.to_timezone requires `input`")
            source = _parse_iso_or_rfc(args.input)
            if source.tzinfo is None:
                source = source.replace(tzinfo=UTC)
            converted = source.astimezone(tz)
            return DatetimeResponse(
                op="to_timezone",
                iso_string=converted.isoformat(),
                epoch_seconds=converted.timestamp(),
                timezone_name=tz_name,
            )

        if args.op == "is_dst":
            if not args.input:
                raise PermissionError("datetime.is_dst requires `input`")
            parsed = _parse_iso_or_rfc(args.input)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=tz)
            return DatetimeResponse(
                op="is_dst",
                iso_string=parsed.isoformat(),
                is_dst=bool(parsed.dst()),
                timezone_name=str(parsed.tzinfo) if parsed.tzinfo else tz_name,
            )

        raise PermissionError(f"unknown datetime op {args.op!r}")


def _resolve_timezone(
    name: str,
    *,
    allow_arbitrary: bool,
    allowlist: set[str],
) -> ZoneInfo | timezone:
    if not allow_arbitrary and name not in allowlist and name != "UTC":
        raise PermissionError(
            f"datetime: timezone {name!r} not in operator allowlist"
        )
    if name == "UTC":
        return UTC
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError as exc:
        raise PermissionError(f"datetime: unknown timezone {name!r}") from exc


def _parse_iso_or_rfc(value: str) -> datetime:
    # Python 3.12 handles most ISO shapes directly.
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        pass
    # Fall back to email.utils for RFC 2822 dates.
    from email.utils import parsedate_to_datetime

    try:
        return parsedate_to_datetime(value)
    except (TypeError, ValueError) as exc:
        raise PermissionError(f"datetime: unparseable input {value!r}") from exc
