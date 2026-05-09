"""Deliverables directory watcher.

Watches the data volume's ``deliverables`` subdirectory for new files and
fires a ``DOWNLOAD_READY`` notification per new file. Debounced so a
single atomic write doesn't fire twice.

Runs as a background ``asyncio.Task`` owned by the web app's lifespan
handler. Cleanup is automatic on shutdown.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import structlog
from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from spark.notifications.kinds import NotificationKind
from spark.notifications.service import get_notification_service

log = structlog.get_logger("spark.notifications.deliverables_watcher")


class _DeliverablesHandler(FileSystemEventHandler):
    def __init__(self, queue: asyncio.Queue[Path]) -> None:
        super().__init__()
        self._queue = queue
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def _enqueue(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        if self._loop is None:
            return
        path = Path(event.src_path)
        self._loop.call_soon_threadsafe(self._queue.put_nowait, path)

    def on_created(self, event: FileSystemEvent) -> None:
        self._enqueue(event)

    def on_moved(self, event: FileSystemEvent) -> None:
        # Atomic writes land as move — watch for the final filename.
        self._enqueue(event)


class DeliverablesWatcher:
    """Background task that fires ``DOWNLOAD_READY`` per new file."""

    def __init__(self, deliverables_path: Path, *, debounce_seconds: float = 2.0) -> None:
        self._path = deliverables_path
        self._debounce = debounce_seconds
        self._queue: asyncio.Queue[Path] = asyncio.Queue()
        self._observer: Any | None = None
        self._consumer_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """Spin up the watcher. Idempotent."""
        if self._consumer_task is not None:
            return
        self._path.mkdir(parents=True, exist_ok=True)
        loop = asyncio.get_running_loop()

        handler = _DeliverablesHandler(self._queue)
        handler.bind_loop(loop)
        observer = Observer()
        observer.schedule(handler, str(self._path), recursive=True)
        observer.daemon = True
        observer.start()
        self._observer = observer

        self._consumer_task = asyncio.create_task(self._consume(), name="deliverables-watcher")
        log.info("deliverables_watcher_started", path=str(self._path))

    async def stop(self) -> None:
        if self._observer is not None:
            self._observer.stop()
            self._observer.join(timeout=2.0)
            self._observer = None
        if self._consumer_task is not None:
            self._consumer_task.cancel()
            try:
                await self._consumer_task
            except (asyncio.CancelledError, Exception):
                pass
            self._consumer_task = None

    async def _consume(self) -> None:
        """Pull events from the queue, debounce, and fire notifications."""
        seen: dict[Path, float] = {}
        svc = get_notification_service()
        while True:
            path = await self._queue.get()
            # Debounce: if we saw the same path within the window, ignore.
            now = asyncio.get_running_loop().time()
            last = seen.get(path, 0.0)
            if now - last < self._debounce:
                continue
            seen[path] = now
            try:
                if not path.exists():
                    continue
                rel = path.relative_to(self._path) if self._path in path.parents or path == self._path else path.name
                rel_str = str(rel)
                # Skip files the engine already recorded — those have a
                # DeliverableRow and notifying again would double-fire
                # DOWNLOAD_READY for every run that produced output.
                if await _is_engine_written(rel_str):
                    continue
                await svc.notify(
                    NotificationKind.DOWNLOAD_READY,
                    title=f"New deliverable: {path.name}",
                    body=f"{rel_str}",
                    severity="info",
                    target_kind="deliverable",
                    target_id=rel_str,
                    # Land on the Downloads page; no per-file route exists
                    # in the SPA, and the relative_path is already in
                    # ``target_id`` for any future deep-link feature.
                    action_url="/downloads",
                )
            except Exception as exc:
                log.warning("deliverables_notify_failed", path=str(path), error=str(exc))


async def _is_engine_written(relative_path: str) -> bool:
    """Return True if the given path corresponds to an engine-written
    DeliverableRow. Engine writes already produce TASK_COMPLETED and
    appear in the run replay, so we don't want a parallel
    DOWNLOAD_READY notification for the same artifact."""
    try:
        from sqlalchemy import select  # noqa: PLC0415

        from spark.persistence.db import session_scope  # noqa: PLC0415
        from spark.persistence.models import DeliverableRow  # noqa: PLC0415

        async with session_scope() as session:
            stmt = select(DeliverableRow).where(
                DeliverableRow.relative_path == relative_path,
                DeliverableRow.source == "engine",
            )
            result = await session.execute(stmt)
            return result.scalars().first() is not None
    except Exception:  # pragma: no cover — best-effort
        return False
