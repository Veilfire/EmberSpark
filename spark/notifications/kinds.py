"""Notification kind enum.

Every kind is a 1:1 column in :class:`NotificationPreferencesRow` so the
user can toggle each category independently.
"""

from __future__ import annotations

from enum import Enum


class NotificationKind(str, Enum):
    DOWNLOAD_READY = "download_ready"
    HITL_SKILL_REVIEW = "hitl_skill_review"
    HITL_APPROVAL = "hitl_approval"
    HITL_DLQ = "hitl_dlq"
    IP_GRANT_EXPIRING = "ip_grant_expiring"
    RAW_LOGGING_ON = "raw_logging_on"
    COST_SOFT_ALERT = "cost_soft_alert"
    COST_HARD_STOP = "cost_hard_stop"
    INCIDENT = "incident"
    PLUGIN_HASH_CHANGED = "plugin_hash_changed"
    MEMORY_PRUNED = "memory_pruned"
    MEMORY_CONTRADICTION = "memory_contradiction"
    MEMORY_REVIEW_NEEDED = "memory_review_needed"
    DATA_CLASS_BLOCKED = "data_class_blocked"
    DATA_CLASS_GRANT_EXPIRING = "data_class_grant_expiring"
