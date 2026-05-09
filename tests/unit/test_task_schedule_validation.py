"""Tests for the mode-aware schedule constraints on TaskSpec.

These rules are enforced both by the YAML loader and by the
``POST /api/scheduler/tasks`` create endpoint, so the validator is the
single source of truth.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from spark.config.enums import TaskMode
from spark.config.models import (
    CronSchedule,
    IntervalSchedule,
    Metadata,
    Task,
    TaskSpec,
)


def _now() -> datetime:
    return datetime.now(tz=timezone.utc).replace(microsecond=0)


def _spec(**kw):  # type: ignore[no-untyped-def]
    return TaskSpec(agent="a", objective="o", **kw)


# ---------------------------------------------------------------------------
# one_shot
# ---------------------------------------------------------------------------


def test_one_shot_no_schedule_ok() -> None:
    s = _spec(mode=TaskMode.ONE_SHOT)
    assert s.mode is TaskMode.ONE_SHOT


def test_one_shot_with_start_only_ok() -> None:
    """Delayed one-shot — start_at is allowed."""
    s = _spec(
        mode=TaskMode.ONE_SHOT,
        schedule=CronSchedule(
            expression="0 0 * * *",
            start_at=_now() + timedelta(hours=1),
        ),
    )
    assert s.schedule is not None
    assert s.schedule.start_at is not None
    assert s.schedule.end_at is None


def test_one_shot_with_end_at_rejected() -> None:
    with pytest.raises(ValueError, match="one_shot tasks cannot have schedule.end_at"):
        _spec(
            mode=TaskMode.ONE_SHOT,
            schedule=CronSchedule(
                expression="0 0 * * *",
                end_at=_now() + timedelta(hours=1),
            ),
        )


# ---------------------------------------------------------------------------
# recurring
# ---------------------------------------------------------------------------


def test_recurring_without_schedule_rejected() -> None:
    with pytest.raises(ValueError, match="recurring tasks require a schedule"):
        _spec(mode=TaskMode.RECURRING)


def test_recurring_without_start_rejected() -> None:
    with pytest.raises(ValueError, match="both schedule.start_at and schedule.end_at"):
        _spec(
            mode=TaskMode.RECURRING,
            schedule=CronSchedule(
                expression="0 0 * * *",
                end_at=_now() + timedelta(days=7),
            ),
        )


def test_recurring_without_end_rejected() -> None:
    with pytest.raises(ValueError, match="both schedule.start_at and schedule.end_at"):
        _spec(
            mode=TaskMode.RECURRING,
            schedule=CronSchedule(
                expression="0 0 * * *",
                start_at=_now(),
            ),
        )


def test_recurring_start_after_end_rejected() -> None:
    with pytest.raises(ValueError, match="start_at must precede end_at"):
        _spec(
            mode=TaskMode.RECURRING,
            schedule=CronSchedule(
                expression="0 0 * * *",
                start_at=_now() + timedelta(days=7),
                end_at=_now() + timedelta(days=1),
            ),
        )


def test_recurring_with_finite_window_ok() -> None:
    s = _spec(
        mode=TaskMode.RECURRING,
        schedule=CronSchedule(
            expression="0 0 * * *",
            start_at=_now(),
            end_at=_now() + timedelta(days=7),
        ),
    )
    assert s.schedule is not None
    assert s.schedule.start_at is not None and s.schedule.end_at is not None


# ---------------------------------------------------------------------------
# perpetual
# ---------------------------------------------------------------------------


def test_perpetual_without_schedule_rejected() -> None:
    with pytest.raises(ValueError, match="perpetual tasks require a schedule"):
        _spec(mode=TaskMode.PERPETUAL)


def test_perpetual_without_start_rejected() -> None:
    with pytest.raises(ValueError, match="perpetual tasks require schedule.start_at"):
        _spec(
            mode=TaskMode.PERPETUAL,
            schedule=CronSchedule(expression="0 0 * * *"),
        )


def test_perpetual_with_end_rejected() -> None:
    with pytest.raises(ValueError, match="perpetual tasks cannot have schedule.end_at"):
        _spec(
            mode=TaskMode.PERPETUAL,
            schedule=CronSchedule(
                expression="0 0 * * *",
                start_at=_now(),
                end_at=_now() + timedelta(days=7),
            ),
        )


def test_perpetual_with_start_only_ok() -> None:
    s = _spec(
        mode=TaskMode.PERPETUAL,
        schedule=CronSchedule(
            expression="0 0 * * *",
            start_at=_now(),
        ),
    )
    assert s.schedule is not None
    assert s.schedule.start_at is not None
    assert s.schedule.end_at is None


# ---------------------------------------------------------------------------
# event
# ---------------------------------------------------------------------------


def test_event_with_schedule_rejected() -> None:
    with pytest.raises(ValueError, match="event tasks fire from external triggers"):
        _spec(
            mode=TaskMode.EVENT,
            schedule=CronSchedule(expression="0 0 * * *"),
        )


# ---------------------------------------------------------------------------
# Interval schedule honors window fields too
# ---------------------------------------------------------------------------


def test_interval_recurring_with_window_ok() -> None:
    s = _spec(
        mode=TaskMode.RECURRING,
        schedule=IntervalSchedule(
            seconds=3600,
            start_at=_now(),
            end_at=_now() + timedelta(days=1),
        ),
    )
    assert isinstance(s.schedule, IntervalSchedule)
    assert s.schedule.seconds == 3600


# ---------------------------------------------------------------------------
# Top-level Task wrapper still validates
# ---------------------------------------------------------------------------


def test_full_task_round_trip() -> None:
    task = Task(
        metadata=Metadata(name="my-task"),
        spec=_spec(
            mode=TaskMode.RECURRING,
            schedule=CronSchedule(
                expression="0 8 * * 1",
                start_at=_now(),
                end_at=_now() + timedelta(weeks=4),
            ),
        ),
    )
    dumped = task.model_dump_json()
    assert "my-task" in dumped
