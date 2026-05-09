"""HTTP polling event source.

Polls a URL on a fixed interval, diffs the returned JSON list against the
last seen set of keys, and fires ``on_fire`` for each new row. Uses the
same SSRF defense as the ``http_client`` plugin.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Awaitable, Callable

import httpx

from spark.config.models import HttpNewRowEvent
from spark.logging import EventType, get_logger
from spark.utils.net import HostPolicy, validate_url

log = get_logger("spark.scheduler.events.http_new_row")


def _extract_key(row: Any, key_path: str) -> str | None:
    """Walk a dotted path through a JSON object to pull a primary key."""
    if not isinstance(row, dict):
        return None
    value: Any = row
    for piece in key_path.split("."):
        if not isinstance(value, dict):
            return None
        if piece not in value:
            return None
        value = value[piece]
    return str(value) if value is not None else None


async def run_http_poller(
    task_name: str,
    event: HttpNewRowEvent,
    on_fire: Callable[[dict[str, Any]], Awaitable[None]],
) -> None:
    policy = HostPolicy.from_list(event.allow_hosts, allow_http=False)
    try:
        target = validate_url(event.url, policy)
    except Exception as exc:
        log.warning("http_new_row url denied", task=task_name, error=str(exc))
        return

    seen: set[str] = set()
    first_pass = True

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(connect=5.0, read=15.0, write=5.0, pool=5.0),
        verify=True,
        trust_env=False,
    ) as client:
        while True:
            try:
                resp = await client.get(target.url)
                resp.raise_for_status()
                data = resp.json()
            except Exception as exc:
                log.warning("http_new_row poll failed", task=task_name, error=str(exc))
                await asyncio.sleep(event.poll_seconds)
                continue

            rows: list[Any] = data if isinstance(data, list) else data.get("items", [])
            new_rows: list[Any] = []
            for row in rows:
                key = _extract_key(row, event.key_path)
                if key is None:
                    continue
                if key not in seen:
                    if not first_pass:
                        new_rows.append(row)
                    seen.add(key)
            first_pass = False

            if new_rows:
                log.info(
                    "event_trigger.fire",
                    event_type=EventType.EVENT_TRIGGER_FIRED,
                    task=task_name,
                    source="http_new_row",
                    new_rows=len(new_rows),
                )
                try:
                    await on_fire({"task": task_name, "new_rows": new_rows[:50]})
                except Exception as exc:
                    log.warning("on_fire failed", task=task_name, error=str(exc))

            try:
                await asyncio.sleep(event.poll_seconds)
            except asyncio.CancelledError:
                raise
