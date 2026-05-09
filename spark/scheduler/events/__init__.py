"""Event-trigger sources for Spark tasks.

Each source exposes a ``run(task_name, event, on_fire)`` coroutine. The source
calls ``await on_fire(payload_dict)`` whenever its trigger condition is met.
The scheduler owns the async task lifetime and handles cancellation.
"""

from __future__ import annotations

from spark.scheduler.events.file_changed import run_file_watcher
from spark.scheduler.events.http_new_row import run_http_poller
from spark.scheduler.events.telegram_bot import run_telegram_bot, upconvert_legacy
from spark.scheduler.events.telegram_message import run_telegram_poller

__all__ = [
    "run_file_watcher",
    "run_http_poller",
    "run_telegram_bot",
    "run_telegram_poller",
    "upconvert_legacy",
]
