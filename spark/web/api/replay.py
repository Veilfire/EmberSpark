"""Run replay + flame graph data."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select

from spark.persistence.db import session_scope
from spark.persistence.learning_repos import (
    ModelCallEventRepository,
    RunSpanRepository,
)
from spark.persistence.models import DeliverableRow, TaskRunRow
from spark.web.auth import Principal, require_viewer

router = APIRouter()


@router.get("/{run_id}")
async def get_run_replay(
    run_id: str, _: Principal = Depends(require_viewer)
) -> dict[str, object]:
    """Return the run's spans, model output, and any artifacts it produced."""
    async with session_scope() as session:
        run = await session.get(TaskRunRow, run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="run not found")
        spans = await RunSpanRepository(session).list_for_run(run_id)
        deliverables_result = await session.execute(
            select(DeliverableRow).where(DeliverableRow.run_id == run_id)
        )
        deliverables = list(deliverables_result.scalars().all())
        model_call_rows = await ModelCallEventRepository(session).list_for_run(run_id)

    # Cost summary block. ``computed`` rows came from the local price
    # table; ``reported`` rows came back from a provider's authoritative
    # response (currently only OpenRouter). The UI surfaces the mix so
    # operators can see when an unknown-cost row is dragging the total.
    total_known = sum(r.cost_usd or 0.0 for r in model_call_rows if r.cost_usd is not None)
    sources: dict[str, int] = {}
    for r in model_call_rows:
        sources[r.cost_source] = sources.get(r.cost_source, 0) + 1
    cost_block = {
        "total_usd": total_known,
        "currency": "USD",
        "source_mix": sources,  # e.g. {"computed": 3, "reported": 5}
        "call_count": len(model_call_rows),
    }

    model_calls = [
        {
            "id": m.id,
            "sequence": m.sequence,
            "started_at": m.started_at,
            "finished_at": m.finished_at,
            "latency_ms": m.latency_ms,
            "provider": m.provider,
            "model": m.model,
            "request_id": m.request_id,
            "input_tokens": m.input_tokens,
            "output_tokens": m.output_tokens,
            "cached_input_tokens": m.cached_input_tokens,
            "cache_creation_tokens": m.cache_creation_tokens,
            "reasoning_tokens": m.reasoning_tokens,
            "cost_usd": m.cost_usd,
            "cost_source": m.cost_source,
        }
        for m in model_call_rows
    ]

    return {
        "run_id": run_id,
        "task_name": run.task_name,
        "agent_name": run.agent_name,
        "state": run.state,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "iterations": run.iterations,
        "model_calls_count": run.model_calls,
        "model_calls": run.model_calls,  # legacy field kept for old UI clients
        "tool_calls": run.tool_calls,
        "summary": run.summary,
        "result_text": run.result_text,
        "trigger_payload_json": run.trigger_payload_json,
        "triggered_by": run.triggered_by,
        "error": run.error,
        "cost": cost_block,
        "model_call_events": model_calls,
        "deliverables": [
            {
                "id": d.id,
                "relative_path": d.relative_path,
                "size_bytes": d.size_bytes,
                "kind": d.kind,
                "source": d.source,
                "created_at": d.created_at,
            }
            for d in deliverables
        ],
        "spans": [
            {
                "id": s.id,
                "parent_span_id": s.parent_span_id,
                "name": s.name,
                "started_at": s.started_at,
                "finished_at": s.finished_at,
                "duration_ms": s.duration_ms,
                "attributes": s.attributes,
                "error_class": s.error_class,
            }
            for s in spans
        ],
    }
