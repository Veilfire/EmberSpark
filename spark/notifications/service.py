"""`NotificationService` — the write seam for every producer.

Every subsystem that wants to surface a HITL-worthy event imports
:func:`get_notification_service` and calls ``await svc.notify(...)``.

The service:

1. Loads the singleton ``NotificationPreferencesRow``. If the per-kind
   column is ``False``, the call is a silent no-op. No row is written;
   no SSE event fires.
2. Otherwise, inserts a row into ``notifications`` and publishes a
   ``notification.created`` event on the SSE bus so the frontend updates
   its bell badge in real time.

Non-critical failures are swallowed and logged — the notification path
must NEVER break a tool call or a scheduler fire. A DB error during
``notify()`` is bad, but bringing down a run because the bell was broken
is worse.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import structlog

from spark.notifications.kinds import NotificationKind
from spark.persistence.db import session_scope
from spark.persistence.learning_models import (
    NotificationPreferencesRow,
    NotificationRow,
)
from spark.web.events import get_bus

log = structlog.get_logger("spark.notifications")


# Allowed action_url shapes: a relative path starting with '/' that does NOT
# begin with '//'. Rejects `javascript:`, `data:`, `file:`, absolute URLs,
# protocol-relative URLs (`//evil.com/foo`), and anything else that could
# render as an arbitrary navigation in the web UI.
def _sanitize_action_url(action_url: str | None) -> str | None:
    if action_url is None:
        return None
    value = action_url.strip()
    if not value:
        return None
    # Must start with a single leading slash
    if not value.startswith("/"):
        return None
    # Reject protocol-relative (`//evil.com`)
    if value.startswith("//"):
        return None
    # Reject control characters that could break HTML/JSON contexts
    if any(ch in value for ch in ("\n", "\r", "\x00", "\\")):
        return None
    # Length cap — matches the DB column
    if len(value) > 512:
        return None
    return value


class NotificationService:
    """Thin orchestrator over the notifications table + SSE bus."""

    async def notify(
        self,
        kind: NotificationKind,
        *,
        title: str,
        body: str | None = None,
        severity: str = "info",
        target_kind: str | None = None,
        target_id: str | None = None,
        action_url: str | None = None,
    ) -> NotificationRow | None:
        """Record a notification and fan out to the SSE bus.

        Returns the inserted row, or ``None`` if the user has opted out
        of this kind. Failures return ``None`` with a structured log.
        """
        try:
            prefs = await self._load_preferences()
            if not getattr(prefs, kind.value, True):
                return None

            # Sanitize any potentially-unsafe fields before persisting.
            # - action_url is gated to relative-path-only (no javascript:,
            #   no protocol-relative, no CRLF).
            # - title is length-capped to the DB column ceiling.
            sanitized_action_url = _sanitize_action_url(action_url)
            if action_url is not None and sanitized_action_url is None:
                log.warning(
                    "notify_rejected_action_url",
                    kind=kind.value,
                    action_url=action_url,
                )

            row = NotificationRow(
                kind=kind.value,
                severity=severity,
                title=title[:200],
                body=body,
                target_kind=target_kind,
                target_id=target_id,
                action_url=sanitized_action_url,
                created_at=_utcnow(),
            )
            async with session_scope() as session:
                session.add(row)
                await session.flush()
                row_id = row.id
                row_dict = _row_to_dict(row)

            bus = get_bus()
            # NOTE: ``EventBus.publish(self, kind: str, **payload)`` —
            # the first positional becomes the event-name. Passing a
            # ``kind=`` kwarg collides with that and raises TypeError.
            # We use ``notification_kind`` for the per-row category and
            # the frontend hook unwraps ``payload.kind`` from there.
            bus.publish(
                "notification.created",
                id=row_id,
                notification_kind=kind.value,
                severity=severity,
                title=row.title,
                body=row.body,
                target_kind=target_kind,
                target_id=target_id,
                action_url=sanitized_action_url,
                created_at=row_dict["created_at"],
            )
            return row
        except Exception as exc:  # pragma: no cover — best-effort
            log.warning("notify_failed", kind=kind.value, error=str(exc))
            return None

    async def _load_preferences(self) -> NotificationPreferencesRow:
        """Return the singleton preferences row, creating it on first call."""
        from sqlalchemy import select

        async with session_scope() as session:
            result = await session.execute(
                select(NotificationPreferencesRow).where(
                    NotificationPreferencesRow.id == 1
                )
            )
            row = result.scalars().first()
            if row is None:
                row = NotificationPreferencesRow(id=1)
                session.add(row)
                await session.flush()
            return row


_service: NotificationService | None = None


def get_notification_service() -> NotificationService:
    """Process-scoped singleton. Cheap to construct but let's not re-wire."""
    global _service
    if _service is None:
        _service = NotificationService()
    return _service


def _utcnow() -> datetime:
    return datetime.now(tz=UTC)


def _row_to_dict(row: NotificationRow) -> dict[str, Any]:
    from spark.utils.time import isoformat as _iso  # noqa: PLC0415

    return {
        "id": row.id,
        "kind": row.kind,
        "severity": row.severity,
        "title": row.title,
        "body": row.body,
        "target_kind": row.target_kind,
        "target_id": row.target_id,
        "action_url": row.action_url,
        "created_at": _iso(row.created_at) if row.created_at else None,
    }
