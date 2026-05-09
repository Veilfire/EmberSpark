"""Periodic watchdog: expiring IP grants + raw logging left on.

Runs on an asyncio timer; checks:

- Any ``internal_network_grants`` row with ``active=True`` and
  ``expires_at`` within the next hour → fire ``IP_GRANT_EXPIRING``.
- ``global_posture.allow_raw_logging=True`` for more than 24h
  (``updated_at`` older than 24h ago) → fire ``RAW_LOGGING_ON``.

De-duplicates by (kind, target_id) against the notifications table so the
operator sees one alert per grant / posture change, not one per tick.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import select

from spark.notifications.kinds import NotificationKind
from spark.notifications.service import get_notification_service
from spark.persistence.db import session_scope
from spark.persistence.learning_models import (
    DataClassGrantRow,
    GlobalPostureRow,
    InternalNetworkGrantRow,
    NotificationRow,
)

log = structlog.get_logger("spark.notifications.grants_watcher")


class GrantsWatcher:
    """Background task that emits HITL notifications periodically."""

    def __init__(self, *, interval_seconds: float = 300.0) -> None:
        self._interval = interval_seconds
        self._task: asyncio.Task[None] | None = None
        self._stopping = asyncio.Event()

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stopping.clear()
        self._task = asyncio.create_task(self._run(), name="grants-watcher")
        log.info("grants_watcher_started", interval_seconds=self._interval)

    async def stop(self) -> None:
        if self._task is None:
            return
        self._stopping.set()
        self._task.cancel()
        try:
            await self._task
        except (asyncio.CancelledError, Exception):
            pass
        self._task = None

    async def _run(self) -> None:
        while not self._stopping.is_set():
            try:
                await self._check_grants()
                await self._check_raw_logging()
                await self._check_data_class_grants()
            except Exception as exc:  # pragma: no cover
                log.warning("grants_watcher_tick_failed", error=str(exc))
            try:
                await asyncio.wait_for(self._stopping.wait(), timeout=self._interval)
                return  # stopping
            except TimeoutError:
                continue

    async def _check_grants(self) -> None:
        svc = get_notification_service()
        threshold = datetime.now(tz=UTC) + timedelta(hours=1)
        async with session_scope() as session:
            result = await session.execute(
                select(InternalNetworkGrantRow)
                .where(InternalNetworkGrantRow.active == True)  # noqa: E712
                .where(InternalNetworkGrantRow.expires_at <= threshold)
            )
            grants = list(result.scalars().all())

        for grant in grants:
            target_id = f"grant-{grant.id}"
            if await self._already_notified(NotificationKind.IP_GRANT_EXPIRING, target_id):
                continue
            from spark.utils.time import isoformat as _iso  # noqa: PLC0415

            expires_iso = _iso(grant.expires_at) if grant.expires_at else "unknown"
            await svc.notify(
                NotificationKind.IP_GRANT_EXPIRING,
                title=f"IP grant expiring: {grant.agent_name} → {grant.cidr}",
                body=f"Grant expires at {expires_iso}. Reason: {grant.reason}",
                severity="elevated",
                target_kind="grant",
                target_id=target_id,
                action_url=f"/security?tab=network&grant={grant.id}",
            )

    async def _check_raw_logging(self) -> None:
        svc = get_notification_service()
        cutoff = datetime.now(tz=UTC) - timedelta(hours=24)
        async with session_scope() as session:
            result = await session.execute(
                select(GlobalPostureRow).where(GlobalPostureRow.id == 1)
            )
            posture = result.scalars().first()
        if posture is None or not posture.allow_raw_logging:
            return
        if posture.updated_at is None:
            return
        posture_updated = posture.updated_at
        if posture_updated.tzinfo is None:
            posture_updated = posture_updated.replace(tzinfo=UTC)
        if posture_updated > cutoff:
            return  # enabled within the last 24h; too soon to remind
        target_id = f"raw-logging-{int(posture_updated.timestamp())}"
        if await self._already_notified(NotificationKind.RAW_LOGGING_ON, target_id):
            return
        await svc.notify(
            NotificationKind.RAW_LOGGING_ON,
            title="Raw logging still enabled",
            body=(
                "allow_raw_logging has been on for > 24 hours. Consider turning "
                "it off if you're done debugging."
            ),
            severity="elevated",
            target_kind="posture",
            target_id=target_id,
            action_url="/security?tab=global-posture",
        )

    async def _check_data_class_grants(self) -> None:
        """Mirror of _check_grants for DataClassGrantRow. Null expires_at
        (permanent grants) are never flagged."""
        svc = get_notification_service()
        threshold = datetime.now(tz=UTC) + timedelta(hours=24)
        async with session_scope() as session:
            result = await session.execute(
                select(DataClassGrantRow)
                .where(DataClassGrantRow.active == True)  # noqa: E712
                .where(DataClassGrantRow.expires_at.is_not(None))  # type: ignore[attr-defined]
                .where(DataClassGrantRow.expires_at <= threshold)
            )
            grants = list(result.scalars().all())

        from spark.utils.time import isoformat as _iso  # noqa: PLC0415

        for g in grants:
            target_id = f"data-grant-{g.id}"
            if await self._already_notified(
                NotificationKind.DATA_CLASS_GRANT_EXPIRING, target_id
            ):
                continue
            expires_iso = _iso(g.expires_at) if g.expires_at else "unknown"
            await svc.notify(
                NotificationKind.DATA_CLASS_GRANT_EXPIRING,
                title=(
                    f"Data-class grant expiring: {g.agent_name} → {g.data_class}"
                ),
                body=(
                    f"Grant expires at {expires_iso}. Reason: {g.reason}. "
                    "Extend it in Security Center → Data Classes or let it lapse."
                ),
                severity="elevated",
                target_kind="data_class_grant",
                target_id=target_id,
                action_url="/security?tab=data-classes",
            )

    async def _already_notified(self, kind: NotificationKind, target_id: str) -> bool:
        async with session_scope() as session:
            result = await session.execute(
                select(NotificationRow.id)
                .where(NotificationRow.kind == kind.value)
                .where(NotificationRow.target_id == target_id)
                .where(NotificationRow.dismissed_at.is_(None))
            )
            return result.scalars().first() is not None
