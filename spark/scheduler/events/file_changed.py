"""File-system event source using `watchdog`.

Debounced: a burst of events within `debounce_seconds` collapses to one fire.
Runs the blocking watchdog observer in a thread and hands events to the
async loop via `run_coroutine_threadsafe`.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any, Awaitable, Callable

from spark.config.models import FileChangedEvent
from spark.logging import EventType, get_logger

log = get_logger("spark.scheduler.events.file_changed")


async def run_file_watcher(
    task_name: str,
    event: FileChangedEvent,
    on_fire: Callable[[dict[str, Any]], Awaitable[None]],
) -> None:
    """Watch a path and fire ``on_fire`` per debounce window."""
    from watchdog.events import FileSystemEventHandler
    from watchdog.observers import Observer

    loop = asyncio.get_running_loop()
    debounce = float(event.debounce_seconds)
    last_fired = 0.0
    pending_events: list[str] = []
    lock = asyncio.Lock()

    async def _maybe_fire() -> None:
        nonlocal last_fired, pending_events
        async with lock:
            if not pending_events:
                return
            now = time.monotonic()
            if now - last_fired < debounce:
                return
            payload = {
                "task": task_name,
                "changes": pending_events[:50],
                "change_count": len(pending_events),
            }
            pending_events = []
            last_fired = now
        log.info(
            "event_trigger.fire",
            event_type=EventType.EVENT_TRIGGER_FIRED,
            task=task_name,
            source="file_changed",
        )
        await on_fire(payload)

    class _Handler(FileSystemEventHandler):
        def on_any_event(self, fs_event: Any) -> None:  # type: ignore[override]
            asyncio.run_coroutine_threadsafe(_enqueue(str(fs_event.src_path)), loop)

    async def _enqueue(path: str) -> None:
        async with lock:
            if len(pending_events) < 500:
                pending_events.append(path)

    path = Path(str(event.path)).expanduser()
    if not path.exists():
        log.warning("file_changed path does not exist", path=str(path))
        return

    observer = Observer()
    observer.schedule(_Handler(), str(path), recursive=event.recursive)
    observer.start()
    try:
        while True:
            await asyncio.sleep(max(1.0, debounce))
            await _maybe_fire()
    except asyncio.CancelledError:
        raise
    finally:
        observer.stop()
        observer.join(timeout=2.0)
