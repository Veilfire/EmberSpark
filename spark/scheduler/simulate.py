"""Schedule simulation — returns predicted fire times without persisting."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from spark.config.models import CronSchedule, IntervalSchedule, ScheduleConfig


def simulate(schedule: ScheduleConfig, horizon_hours: int = 24 * 7) -> list[datetime]:
    """Return a list of upcoming fire times within `horizon_hours`.

    Uses APScheduler's internal next-fire-time iterator. The simulation never
    touches the real scheduler state.
    """
    tz = ZoneInfo(schedule.timezone)
    end = datetime.now(tz=timezone.utc) + timedelta(hours=horizon_hours)
    fires: list[datetime] = []

    if isinstance(schedule, CronSchedule):
        from apscheduler.triggers.cron import CronTrigger

        trigger = CronTrigger.from_crontab(schedule.expression, timezone=tz)
        current: datetime | None = None
        while True:
            nxt: datetime | None = trigger.get_next_fire_time(current, datetime.now(tz=tz))
            if nxt is None or nxt > end.astimezone(tz):
                break
            fires.append(nxt.astimezone(timezone.utc))
            current = nxt
            if len(fires) >= 500:
                break
    elif isinstance(schedule, IntervalSchedule):
        from apscheduler.triggers.interval import IntervalTrigger

        trigger = IntervalTrigger(seconds=schedule.seconds, timezone=tz)
        current = None
        while True:
            nxt = trigger.get_next_fire_time(current, datetime.now(tz=tz))
            if nxt is None or nxt > end.astimezone(tz):
                break
            fires.append(nxt.astimezone(timezone.utc))
            current = nxt
            if len(fires) >= 500:
                break
    return fires


def parse_run_window(spec: str) -> tuple[int, int, str]:
    """Parse ``"HH:MM-HH:MM TZ"`` into ``(start_min, end_min, tz)``.

    Returns times as minutes-since-midnight in the given timezone.
    """
    window, tz_name = spec.rsplit(" ", 1) if " " in spec else (spec, "UTC")
    start_s, end_s = window.split("-")

    def _minutes(hhmm: str) -> int:
        h, m = hhmm.strip().split(":")
        return int(h) * 60 + int(m)

    return _minutes(start_s), _minutes(end_s), tz_name.strip()


def in_window(now_utc: datetime, spec: str) -> bool:
    """True if ``now_utc`` falls inside the run window described by ``spec``."""
    start_min, end_min, tz_name = parse_run_window(spec)
    tz = ZoneInfo(tz_name)
    now_local = now_utc.astimezone(tz)
    minutes = now_local.hour * 60 + now_local.minute
    if start_min == end_min:
        return True
    if start_min < end_min:
        return start_min <= minutes < end_min
    # window wraps midnight
    return minutes >= start_min or minutes < end_min
