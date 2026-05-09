"""OpenRouter post-hoc generation enrichment.

OpenRouter ingests its generation telemetry asynchronously — for ~1-5
seconds after the streaming response completes the ``GET /api/v1/generation``
endpoint may return ``404`` or stale data. The runtime captures the
``gen-…`` ID immediately, persists a per-call row with ``cost_source =
"computed"``, and schedules a fire-and-forget background task that polls the
enrichment endpoint and upgrades the row to ``cost_source = "reported"``.

Failure modes (network, 404 indefinitely, key revoked, ingestion lag past
our retry budget) are intentionally non-fatal: we log a warning and the
local computed cost stays. The runtime does not retry beyond the budget
defined here — operators see the gap as a `computed` row and can refresh
later.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import structlog

log = structlog.get_logger("spark.providers.openrouter_enrich")


GENERATION_URL = "https://openrouter.ai/api/v1/generation"
DEFAULT_INITIAL_DELAY_SECONDS = 2.0
DEFAULT_MAX_ATTEMPTS = 4
DEFAULT_BACKOFF_SECONDS = 2.5
DEFAULT_HTTP_TIMEOUT_SECONDS = 10.0


async def fetch_generation(
    gen_id: str,
    api_key: str,
    *,
    timeout_seconds: float = DEFAULT_HTTP_TIMEOUT_SECONDS,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any] | None:
    """Single GET against ``/api/v1/generation?id={gen_id}``.

    Returns the ``data`` object on 200 OK; ``None`` on any non-200 or
    transport error. The caller is responsible for retry / scheduling.
    Pass an existing ``client`` to share a connection pool.
    """
    params = {"id": gen_id}
    headers = {"Authorization": f"Bearer {api_key}"}
    own_client = client is None
    if own_client:
        client = httpx.AsyncClient(timeout=timeout_seconds, trust_env=False)
    try:
        response = await client.get(GENERATION_URL, params=params, headers=headers)
        if response.status_code != 200:
            return None
        body = response.json()
    except (httpx.RequestError, ValueError) as exc:
        log.debug("openrouter.enrich_fetch_failed", gen_id=gen_id, error=str(exc))
        return None
    finally:
        if own_client:
            await client.aclose()
    if isinstance(body, dict):
        # OpenRouter wraps the payload in {"data": {...}}.
        if isinstance(body.get("data"), dict):
            return body["data"]
        return body
    return None


async def enrich_with_retry(
    gen_id: str,
    api_key: str,
    *,
    initial_delay_seconds: float = DEFAULT_INITIAL_DELAY_SECONDS,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    backoff_seconds: float = DEFAULT_BACKOFF_SECONDS,
) -> dict[str, Any] | None:
    """Wait through the ingestion lag, then poll until we get a payload.

    Returns the parsed ``data`` dict on success, or ``None`` if every
    attempt failed. Total worst-case wall time is roughly
    ``initial_delay_seconds + max_attempts * backoff_seconds``.
    """
    if not gen_id or not api_key:
        return None
    await asyncio.sleep(initial_delay_seconds)
    for attempt in range(max_attempts):
        payload = await fetch_generation(gen_id, api_key)
        if payload is not None:
            return payload
        await asyncio.sleep(backoff_seconds)
    log.info("openrouter.enrich_giveup", gen_id=gen_id, attempts=max_attempts)
    return None
