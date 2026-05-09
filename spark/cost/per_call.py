"""Per-model-call telemetry helpers.

The capture seam is shared between scheduled task runs (driven by
:class:`spark.runtime.engine.RuntimeEngine`) and chat sessions (driven
by :func:`spark.web.api.chat._run_chat_tool_loop`). Both want the same
shape: one ``model_call_events`` row per model invocation with the five
token classes (input / output / cache_read / cache_creation / reasoning),
a request id (``gen-…`` for OpenRouter, ``msg_…`` for Anthropic,
``chatcmpl-…`` for OpenAI), latency, and a USD cost computed locally
unless OpenRouter's authoritative figure is already in the response
metadata.

Pulling these out of the engine into a module-level function lets the
chat path re-use them without dragging in a full ``RuntimeEngine``
instance, and gives us one canonical place to extend later (e.g.
Anthropic batch costs, OpenAI cached-input pricing tweaks).
"""

from __future__ import annotations

import asyncio
import json
import time as _time
from datetime import UTC, datetime
from typing import Any

import structlog

from spark.cost.pricing import compute_cost
from spark.persistence.db import session_scope
from spark.persistence.learning_models import ModelCallEventRow
from spark.persistence.learning_repos import ModelCallEventRepository

log = structlog.get_logger("spark.cost.per_call")


def extract_openrouter_reported_cost(
    response_metadata: dict[str, Any], usage_metadata: dict[str, Any]
) -> float | None:
    """Pull OpenRouter's authoritative ``usage.cost`` out of a response.

    See ``providers/factory.py`` for where ``extra_body={"usage":
    {"include": True}}`` opts in. langchain-openai's exact landing
    place varies across versions, so we check a few plausible
    locations and return the first numeric value > 0 we find.
    """
    candidates: list[Any] = []

    for key in ("token_usage", "usage"):
        block = response_metadata.get(key)
        if isinstance(block, dict):
            candidates.append(block.get("cost"))
            details = block.get("cost_details")
            if isinstance(details, dict):
                candidates.append(details.get("upstream_inference_cost"))
                candidates.append(details.get("total_cost"))

    candidates.append(response_metadata.get("cost"))

    extras = usage_metadata.get("extras")
    if isinstance(extras, dict):
        candidates.append(extras.get("cost"))

    for c in candidates:
        if isinstance(c, (int, float)) and c > 0:
            return float(c)
    return None


def split_usage(response: Any) -> dict[str, Any] | None:
    """Pull the five-bucket usage breakdown + request_id off a LangChain
    AIMessage. Returns ``None`` when the response carries no
    ``usage_metadata`` (typical for stream chunks, not final responses).
    """
    usage = getattr(response, "usage_metadata", None)
    if not isinstance(usage, dict):
        return None
    response_metadata = getattr(response, "response_metadata", None) or {}
    if not isinstance(response_metadata, dict):
        response_metadata = {}

    input_details = usage.get("input_token_details") or {}
    output_details = usage.get("output_token_details") or {}

    request_id = response_metadata.get("id") or response_metadata.get("request_id")

    return {
        "input_tokens": int(usage.get("input_tokens", 0) or 0),
        "output_tokens": int(usage.get("output_tokens", 0) or 0),
        "cached_input_tokens": int(input_details.get("cache_read", 0) or 0),
        "cache_creation_tokens": int(input_details.get("cache_creation", 0) or 0),
        "reasoning_tokens": int(output_details.get("reasoning", 0) or 0),
        "request_id": str(request_id) if request_id else None,
        "usage_metadata": dict(usage),
        "response_metadata": dict(response_metadata),
    }


async def record_model_call(
    *,
    run_id: str,
    sequence: int,
    provider: str,
    model: str,
    response: Any,
    started_at: datetime,
    finished_at: datetime,
    latency_ms: int,
) -> tuple[int, str | None] | None:
    """Persist a ``model_call_events`` row for a single model invocation.

    Returns ``(row_id, request_id)`` so the caller can schedule a deferred
    enrichment when ``provider == "openrouter"`` and the response
    didn't carry an inline ``usage.cost``. Returns ``None`` when the
    response had no usage_metadata at all (nothing to record).
    """
    breakdown = split_usage(response)
    if breakdown is None:
        return None

    reported = extract_openrouter_reported_cost(
        breakdown["response_metadata"], breakdown["usage_metadata"]
    )
    if reported is not None:
        cost_usd: float | None = reported
        cost_source = "reported"
    else:
        cost_usd = compute_cost(
            provider=provider,
            model=model,
            input_tokens=breakdown["input_tokens"],
            output_tokens=breakdown["output_tokens"],
            cached_input_tokens=breakdown["cached_input_tokens"],
            cache_creation_tokens=breakdown["cache_creation_tokens"],
            reasoning_tokens=breakdown["reasoning_tokens"],
        )
        cost_source = "computed"

    raw = json.dumps(
        {
            "usage_metadata": breakdown["usage_metadata"],
            "response_metadata": breakdown["response_metadata"],
        },
        default=str,
    )

    row = ModelCallEventRow(
        run_id=run_id,
        sequence=sequence,
        started_at=started_at,
        finished_at=finished_at,
        latency_ms=latency_ms,
        provider=provider,
        model=model,
        request_id=breakdown["request_id"],
        input_tokens=breakdown["input_tokens"],
        output_tokens=breakdown["output_tokens"],
        cached_input_tokens=breakdown["cached_input_tokens"],
        cache_creation_tokens=breakdown["cache_creation_tokens"],
        reasoning_tokens=breakdown["reasoning_tokens"],
        cost_usd=cost_usd,
        cost_source=cost_source,
        raw_metadata_json=raw,
    )
    async with session_scope() as session:
        await ModelCallEventRepository(session).record(row)

    return row.id or 0, breakdown["request_id"]


def schedule_openrouter_enrichment(
    *,
    row_id: int,
    request_id: str,
    api_key: str,
    tasks: list[asyncio.Task[None]] | None = None,
) -> asyncio.Task[None] | None:
    """Fire-and-forget background task that pulls OpenRouter's
    authoritative cost via /api/v1/generation and updates the row.

    ``tasks`` is an optional list the caller passes in to retain
    references (so the GC doesn't drop them mid-flight); when omitted,
    the task floats and Python keeps it alive via the running loop.
    Returns the created task or ``None`` if no event loop is running.
    """
    from spark.providers.openrouter_enrich import enrich_with_retry

    async def _run() -> None:
        try:
            payload = await enrich_with_retry(request_id, api_key)
            if payload is None:
                return
            cost_value = payload.get("usage")
            if not isinstance(cost_value, (int, float)):
                cost_value = None
            async with session_scope() as session:
                await ModelCallEventRepository(session).update_from_enrichment(
                    row_id=row_id,
                    cost_usd=float(cost_value) if cost_value is not None else None,
                    raw_metadata_merge=payload,
                )
        except Exception as exc:  # pragma: no cover
            log.warning(
                "openrouter.enrich_task_failed", row_id=row_id, error=str(exc)
            )

    try:
        task = asyncio.create_task(_run())
        if tasks is not None:
            tasks.append(task)
        return task
    except RuntimeError:
        return None


def measure_latency_ms(monotonic_start: float) -> int:
    """Convenience helper — used at the call site so both the engine
    and chat compute latency the same way.
    """
    return int((_time.monotonic() - monotonic_start) * 1000)


def utcnow() -> datetime:
    """tz-aware UTC now, matching the rest of the persistence layer."""
    return datetime.now(tz=UTC)
