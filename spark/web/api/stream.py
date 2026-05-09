"""SSE stream for live events + log tail."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import AsyncIterator

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from spark.web.auth import Principal, require_viewer
from spark.web.events import get_bus

router = APIRouter()

LOG_PATH = Path("~/.spark/logs/spark.jsonl").expanduser()


async def _event_stream(request: Request) -> AsyncIterator[str]:
    bus = get_bus()
    queue = await bus.subscribe()
    try:
        while True:
            if await request.is_disconnected():
                break
            try:
                event = await asyncio.wait_for(queue.get(), timeout=15.0)
                # No ``event:`` field — that would route the message to
                # an ``addEventListener(kind, ...)`` handler instead of
                # the EventSource's default ``onmessage``, and the
                # frontend hooks (``useNotifications`` etc.) all use
                # ``onmessage``. The kind is already inside the JSON
                # payload so consumers filter on ``data.kind``.
                data = json.dumps({"kind": event.kind, "payload": event.payload})
                yield f"data: {data}\n\n"
            except asyncio.TimeoutError:
                yield ": keepalive\n\n"
    finally:
        await bus.unsubscribe(queue)


@router.get("/events")
async def events_sse(
    request: Request, _: Principal = Depends(require_viewer)
) -> StreamingResponse:
    return StreamingResponse(
        _event_stream(request),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _log_tail(request: Request) -> AsyncIterator[str]:
    """Stream the JSONL log: backfill the last 50 lines, then tail."""
    if not LOG_PATH.exists():
        yield f"data: {json.dumps({'error': 'no log file yet'})}\n\n"
        return
    with LOG_PATH.open("r", encoding="utf-8") as f:
        # Backfill — without this, an operator opening the Ops page
        # sees an empty pane until something logs within their viewing
        # window. ``readlines()`` is fine for our log sizes (rotated
        # at a few hundred MB worst-case); we slice to the last N so
        # we never push an unbounded chunk on connect.
        try:
            recent = f.readlines()[-50:]
        except OSError:
            recent = []
        for line in recent:
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                payload = {"raw": line.rstrip()}
            yield f"data: {json.dumps(payload)}\n\n"
        # Switch to tail mode — readline() returns "" at EOF, then
        # picks up new lines as the file grows.
        f.seek(0, 2)
        while True:
            if await request.is_disconnected():
                break
            line = f.readline()
            if not line:
                await asyncio.sleep(0.5)
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                payload = {"raw": line.rstrip()}
            yield f"data: {json.dumps(payload)}\n\n"


@router.get("/logs")
async def logs_sse(
    request: Request, _: Principal = Depends(require_viewer)
) -> StreamingResponse:
    return StreamingResponse(
        _log_tail(request),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
