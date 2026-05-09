"""Enumerated event types for the structured log.

Using an enum forces all log emission sites to reference a known constant;
free-string event names are a type error at callsites that accept `EventType`.
"""

from __future__ import annotations

from enum import Enum


class EventType(str, Enum):
    TASK_CREATED = "task.created"
    TASK_STARTED = "task.started"
    TASK_COMPLETED = "task.completed"
    TASK_FAILED = "task.failed"
    TASK_RESCHEDULED = "task.rescheduled"
    MODEL_INVOKED = "model.invoked"
    TOOL_INVOKED = "tool.invoked"
    TOOL_RESULT_RECEIVED = "tool.result_received"
    MEMORY_RETRIEVED = "memory.retrieved"
    MEMORY_PROMOTED = "memory.promoted"
    MEMORY_PRUNED = "memory.pruned"
    REFLECTION_COMPLETED = "reflection.completed"
    PLUGIN_LOADED = "plugin.loaded"
    PLUGIN_HASH_CHANGED = "plugin.hash_changed"
    SECRET_REQUESTED = "secret.requested"
    REDACTION_APPLIED = "redaction.applied"
    SCHEDULER_TICK = "scheduler.tick"
    SANDBOX_INVOKED = "sandbox.invoked"
    SANDBOX_DENIED = "sandbox.denied"
    BUDGET_EXCEEDED = "budget.exceeded"
    PERMISSION_DENIED = "permission.denied"
    # F4 additions
    SPAN_EMITTED = "span.emitted"
    PROMPT_COMPOSED = "prompt.composed"
    BUDGET_TICK = "budget.tick"
    REDACTION_SUMMARY = "redaction.summary"
    FILE_HEADER = "file.header"
    TOOL_ERROR_CLASSIFIED = "tool.error_classified"
    # F5 additions
    TASK_HEARTBEAT = "task.heartbeat"
    TASK_DLQ = "task.dlq"
    TASK_APPROVAL_REQUESTED = "task.approval_requested"
    EVENT_TRIGGER_FIRED = "event_trigger.fired"
    WEBHOOK_FIRED = "webhook.fired"
