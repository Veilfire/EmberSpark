"""Trigger builders for APScheduler.

Cron expressions are parsed manually so we can pass ``start_date`` and
``end_date`` through alongside the cron fields — APScheduler's
``CronTrigger.from_crontab`` classmethod doesn't accept window kwargs.
``IntervalTrigger`` accepts both natively.
"""

from __future__ import annotations

from zoneinfo import ZoneInfo

from spark.config.models import CronSchedule, IntervalSchedule, ScheduleConfig


def _parse_cron_fields(expression: str) -> dict[str, str]:
    """Split a 5-field crontab expression into APScheduler kwargs.

    Mirrors ``CronTrigger.from_crontab`` but exposes the parsed fields
    so we can construct a CronTrigger with ``start_date`` / ``end_date``
    directly.
    """
    parts = expression.strip().split()
    if len(parts) != 5:
        raise ValueError(
            f"cron expression must have 5 whitespace-separated fields "
            f"(minute hour day month day_of_week); got {len(parts)}"
        )
    minute, hour, day, month, day_of_week = parts
    return {
        "minute": minute,
        "hour": hour,
        "day": day,
        "month": month,
        "day_of_week": day_of_week,
    }


def build_trigger(schedule: ScheduleConfig) -> object:
    from apscheduler.triggers.cron import CronTrigger
    from apscheduler.triggers.interval import IntervalTrigger

    tz = ZoneInfo(schedule.timezone)
    if isinstance(schedule, CronSchedule):
        fields = _parse_cron_fields(schedule.expression)
        return CronTrigger(
            **fields,
            timezone=tz,
            start_date=schedule.start_at,
            end_date=schedule.end_at,
        )
    if isinstance(schedule, IntervalSchedule):
        return IntervalTrigger(
            seconds=schedule.seconds,
            timezone=tz,
            start_date=schedule.start_at,
            end_date=schedule.end_at,
        )
    raise TypeError(f"Unknown schedule type {type(schedule).__name__}")
