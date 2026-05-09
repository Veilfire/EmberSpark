"""Notifications — unified HITL / download / incident surface.

Subsystems that produce HITL-worthy events (skill discovery, scheduler
approvals, DLQ transitions, cost budgets, critical audit entries, new files
in the deliverables directory) publish via :class:`NotificationService`.
The service:

1. Consults :class:`NotificationPreferences` — if the user has opted out of
   the given kind, the call is a silent no-op.
2. Writes a row to the ``notifications`` table.
3. Publishes a ``notification.created`` event to the in-process SSE bus
   so the web UI updates its bell badge and fires a toast (if enabled).

Per-kind preferences map 1:1 to the kinds in :mod:`spark.notifications.kinds`.
"""

from __future__ import annotations

from spark.notifications.kinds import NotificationKind
from spark.notifications.service import NotificationService, get_notification_service

__all__ = ["NotificationKind", "NotificationService", "get_notification_service"]
