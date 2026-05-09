"""Scheduler namespace — holds the process-scoped ``SparkScheduler`` singleton.

The runtime scheduler is lazily created by the web app at startup via
``set_scheduler(SparkScheduler())`` and then ``await scheduler.start()``.
API routes that need to inspect jobs (e.g. the memory pruning status
endpoint) call :func:`get_scheduler`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from spark.scheduler.scheduler import SparkScheduler

_scheduler: "SparkScheduler | None" = None


def set_scheduler(scheduler: "SparkScheduler | None") -> None:
    global _scheduler
    _scheduler = scheduler


def get_scheduler() -> "SparkScheduler | None":
    return _scheduler


__all__ = ["set_scheduler", "get_scheduler"]
