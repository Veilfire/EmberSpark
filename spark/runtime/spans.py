"""Span tracing for the engine.

A `Span` is a simple ``(name, start, end, parent)`` record bound to the
current run via an async context variable. Nested spans pick up the parent
automatically. On close, the span is persisted to the ``run_spans`` table
and a ``span.emitted`` log event is emitted.

The structured output feeds the run replay + flame graph pages in F6.
"""

from __future__ import annotations

import asyncio
import contextvars
import json
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

from spark.logging import EventType, get_logger
from spark.persistence.db import session_scope
from spark.persistence.learning_models import RunSpanRow
from spark.persistence.learning_repos import RunSpanRepository
from spark.utils.time import utcnow

log = get_logger("spark.span")

_run_id_ctx: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "spark_run_id", default=None
)
_parent_span_ctx: contextvars.ContextVar[int | None] = contextvars.ContextVar(
    "spark_parent_span_id", default=None
)


@dataclass
class SpanHandle:
    id: int | None
    name: str
    run_id: str
    parent_span_id: int | None
    started_at_mono: float
    attributes: dict[str, Any] = field(default_factory=dict)
    error_class: str | None = None


def set_run_id(run_id: str) -> contextvars.Token[str | None]:
    """Bind a run_id to the current async context; returns a reset token."""
    return _run_id_ctx.set(run_id)


def reset_run_id(token: contextvars.Token[str | None]) -> None:
    _run_id_ctx.reset(token)


def current_run_id() -> str | None:
    return _run_id_ctx.get()


@asynccontextmanager
async def span(name: str, **attributes: Any) -> AsyncIterator[SpanHandle]:
    """Record a span. Nested spans pick up the current parent automatically."""
    run_id = _run_id_ctx.get()
    if run_id is None:
        # Spans outside a run are a no-op; we still yield a handle so
        # callers don't need to branch.
        dummy = SpanHandle(
            id=None,
            name=name,
            run_id="",
            parent_span_id=None,
            started_at_mono=time.monotonic(),
            attributes=dict(attributes),
        )
        yield dummy
        return

    started_at = utcnow()
    started_at_mono = time.monotonic()
    parent_id = _parent_span_ctx.get()

    row = RunSpanRow(
        run_id=run_id,
        parent_span_id=parent_id,
        name=name,
        started_at=started_at,
        attributes=json.dumps(attributes, default=str),
    )
    async with session_scope() as session:
        await RunSpanRepository(session).insert(row)

    handle = SpanHandle(
        id=row.id,
        name=name,
        run_id=run_id,
        parent_span_id=parent_id,
        started_at_mono=started_at_mono,
        attributes=dict(attributes),
    )
    token = _parent_span_ctx.set(row.id)
    error_class: str | None = None
    try:
        yield handle
    except BaseException as exc:
        error_class = handle.error_class or type(exc).__name__
        raise
    finally:
        _parent_span_ctx.reset(token)
        duration_ms = (time.monotonic() - started_at_mono) * 1000.0
        finished_at = utcnow()
        error_class = handle.error_class or error_class
        try:
            async with session_scope() as session:
                stored = await session.get(RunSpanRow, row.id)
                if stored is not None:
                    stored.finished_at = finished_at
                    stored.duration_ms = duration_ms
                    stored.error_class = error_class
                    stored.attributes = json.dumps(
                        handle.attributes, default=str
                    )
        except Exception:  # pragma: no cover
            pass
        log.info(
            "span.emitted",
            event_type=EventType.SPAN_EMITTED,
            run_id=run_id,
            span_id=row.id,
            parent_span_id=parent_id,
            span_name=name,
            duration_ms=round(duration_ms, 3),
            error_class=error_class,
        )


def record_error_class(handle: SpanHandle, error_class: str) -> None:
    """Attach an explicit error class to the in-flight span."""
    handle.error_class = error_class
