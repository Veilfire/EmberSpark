"""Bucketed aggregator that emits `redaction.summary` events periodically.

Every time the structlog chain scrubs an event, it tags ``redaction_applied``
and (optionally) a list of category labels. This module buckets those label
counts in 60-second windows and emits a summary event at window boundaries.

Counts only, **never samples**. The goal is compliance-friendly observability
without leaking the content that was redacted.
"""

from __future__ import annotations

import threading
import time
from collections import Counter
from typing import Any, Callable, MutableMapping


class RedactionAggregator:
    def __init__(
        self,
        *,
        window_seconds: float = 60.0,
        emit: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.window_seconds = window_seconds
        self._counts: Counter[str] = Counter()
        self._started_at = time.monotonic()
        self._lock = threading.Lock()
        self._emit = emit or _default_emit

    def observe(self, labels: list[str] | None) -> None:
        if not labels:
            return
        with self._lock:
            for label in labels:
                self._counts[label] += 1

    def maybe_emit(self) -> None:
        """If a window has passed, emit a summary and reset the counter."""
        now = time.monotonic()
        with self._lock:
            elapsed = now - self._started_at
            if elapsed < self.window_seconds or not self._counts:
                return
            snapshot = dict(self._counts)
            self._counts.clear()
            self._started_at = now
        payload = {
            "window_seconds": self.window_seconds,
            "categories": snapshot,
        }
        self._emit(payload)


def _default_emit(payload: dict[str, Any]) -> None:
    # Lazy import to avoid cycles.
    from spark.logging import EventType, get_logger

    log = get_logger("spark.redaction")
    log.info(
        "redaction.summary",
        event_type=EventType.REDACTION_SUMMARY,
        **payload,
    )


_singleton: RedactionAggregator | None = None


def get_aggregator() -> RedactionAggregator:
    global _singleton
    if _singleton is None:
        _singleton = RedactionAggregator()
    return _singleton


def make_aggregator_processor() -> Callable[
    [Any, str, MutableMapping[str, Any]], MutableMapping[str, Any]
]:
    """structlog processor: feeds the aggregator on every scrubbed event."""
    agg = get_aggregator()

    def processor(
        _logger: Any, _method: str, event_dict: MutableMapping[str, Any]
    ) -> MutableMapping[str, Any]:
        applied = event_dict.get("redaction_applied")
        if applied:
            labels = event_dict.get("redaction_labels") or []
            if not isinstance(labels, list):
                labels = []
            agg.observe(labels)
        agg.maybe_emit()
        return event_dict

    return processor
