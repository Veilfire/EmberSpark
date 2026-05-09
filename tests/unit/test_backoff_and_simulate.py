"""Tests for backoff + schedule simulation + run-window predicates."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from spark.config.models import CronSchedule, IntervalSchedule, RetryPolicy
from spark.scheduler.backoff import backoff_delay, should_give_up
from spark.scheduler.simulate import in_window, parse_run_window, simulate


def test_backoff_grows_exponentially() -> None:
    policy = RetryPolicy(
        max_attempts=5, backoff_seconds=1.0, backoff_multiplier=2.0, jitter_seconds=0.0
    )
    delays = [backoff_delay(policy, i) for i in range(1, 6)]
    assert delays == [1.0, 2.0, 4.0, 8.0, 16.0]


def test_backoff_capped_at_an_hour() -> None:
    policy = RetryPolicy(
        max_attempts=20, backoff_seconds=1.0, backoff_multiplier=10.0, jitter_seconds=0.0
    )
    # 10^15 = way over an hour → cap to 3600.
    assert backoff_delay(policy, 20) <= 3600.0


def test_give_up_at_max_attempts() -> None:
    policy = RetryPolicy(max_attempts=3)
    assert should_give_up(policy, 3) is True
    assert should_give_up(policy, 2) is False


def test_simulate_interval_returns_expected_count() -> None:
    sched = IntervalSchedule(seconds=3600, timezone="UTC")
    # 24h horizon, hourly → 24 fires.
    fires = simulate(sched, horizon_hours=24)
    assert 23 <= len(fires) <= 25  # allow boundary slack


def test_simulate_cron_monday_8am() -> None:
    sched = CronSchedule(expression="0 8 * * 1", timezone="UTC")
    fires = simulate(sched, horizon_hours=24 * 14)
    # 2 Mondays → 2 fires.
    assert len(fires) == 2
    for f in fires:
        assert f.hour == 8 and f.minute == 0
        assert f.weekday() == 0


def test_parse_run_window() -> None:
    assert parse_run_window("22:00-06:00 America/Vancouver") == (
        22 * 60,
        6 * 60,
        "America/Vancouver",
    )


def test_in_window_normal() -> None:
    # 12:00 UTC falls inside 10:00-14:00 UTC
    now = datetime(2026, 4, 1, 12, 0, tzinfo=timezone.utc)
    assert in_window(now, "10:00-14:00 UTC") is True


def test_in_window_wrapping_midnight() -> None:
    # 01:00 UTC inside 22:00-06:00 UTC (wraps)
    now = datetime(2026, 4, 1, 1, 0, tzinfo=timezone.utc)
    assert in_window(now, "22:00-06:00 UTC") is True
    now_day = datetime(2026, 4, 1, 12, 0, tzinfo=timezone.utc)
    assert in_window(now_day, "22:00-06:00 UTC") is False
