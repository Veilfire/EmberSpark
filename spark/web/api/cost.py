"""Cost dashboard routes."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select

from spark.persistence.db import session_scope
from spark.persistence.learning_models import BudgetRow, CostEventRow
from spark.persistence.learning_repos import AuditRepository, BudgetRepository
from spark.web.auth import Principal, require_operator, require_viewer
from spark.web.schemas import BudgetCreate, CostWindowResponse

router = APIRouter()


@router.get("/window/{period}", response_model=CostWindowResponse)
async def cost_window(
    period: str, _: Principal = Depends(require_viewer)
) -> CostWindowResponse:
    if period not in {"day", "week", "month", "all"}:
        raise HTTPException(
            status_code=400, detail="period must be day|week|month|all"
        )
    now = datetime.now(tz=timezone.utc)

    async with session_scope() as session:
        if period == "all":
            # No time filter — sum every CostEventRow ever recorded.
            # Useful for the Overview "Spend (all-time)" card.
            stmt = select(CostEventRow)
        else:
            since = {
                "day": now - timedelta(days=1),
                "week": now - timedelta(days=7),
                "month": now - timedelta(days=30),
            }[period]
            stmt = select(CostEventRow).where(CostEventRow.recorded_at >= since)
        result = await session.execute(stmt)
        rows = list(result.scalars().all())
    by_provider: dict[str, float] = {}
    by_agent: dict[str, float] = {}
    by_model: dict[str, float] = {}
    total = 0.0
    for r in rows:
        by_provider[r.provider] = by_provider.get(r.provider, 0.0) + r.total_cost_usd
        by_agent[r.agent_name] = by_agent.get(r.agent_name, 0.0) + r.total_cost_usd
        by_model[r.model] = by_model.get(r.model, 0.0) + r.total_cost_usd
        total += r.total_cost_usd
    return CostWindowResponse(
        period=period,
        total_usd=total,
        by_provider=by_provider,
        by_agent=by_agent,
        by_model=by_model,
    )


@router.get("/hourly")
async def hourly_cost(
    hours: int = 24, _: Principal = Depends(require_viewer)
) -> dict[str, list[float]]:
    """Cost bucketed by hour for the last N hours (for sparkline charts)."""
    hours = max(1, min(hours, 168))
    now = datetime.now(tz=timezone.utc)
    since = now - timedelta(hours=hours)
    async with session_scope() as session:
        result = await session.execute(
            select(CostEventRow).where(CostEventRow.recorded_at >= since)
        )
        rows = list(result.scalars().all())

    buckets = [0.0] * hours
    for r in rows:
        ts = r.recorded_at
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        delta = (now - ts).total_seconds() / 3600
        idx = hours - 1 - int(delta)
        if 0 <= idx < hours:
            buckets[idx] += r.total_cost_usd or 0
    return {"buckets": buckets}


@router.get("/events")
async def recent_events(
    limit: int = 200, _: Principal = Depends(require_viewer)
) -> list[dict[str, object]]:
    async with session_scope() as session:
        result = await session.execute(
            select(CostEventRow).order_by(CostEventRow.recorded_at.desc()).limit(limit)
        )
        rows = list(result.scalars().all())
    return [
        {
            "run_id": r.run_id,
            "agent": r.agent_name,
            "task": r.task_name,
            "provider": r.provider,
            "model": r.model,
            "total_tokens": r.total_tokens,
            "total_usd": r.total_cost_usd,
            "recorded_at": r.recorded_at,
        }
        for r in rows
    ]


@router.get("/budgets")
async def list_budgets(_: Principal = Depends(require_viewer)) -> list[dict[str, object]]:
    async with session_scope() as session:
        rows = await BudgetRepository(session).list_all()
    return [
        {
            "budget_id": b.budget_id,
            "scope": b.scope,
            "scope_key": b.scope_key,
            "period": b.period,
            "limit_usd": b.limit_usd,
            "soft_alert_usd": b.soft_alert_usd,
            "hard_stop": b.hard_stop,
            "enabled": b.enabled,
        }
        for b in rows
    ]


@router.post("/budgets")
async def create_budget(
    body: BudgetCreate, principal: Principal = Depends(require_operator)
) -> dict[str, bool]:
    row = BudgetRow(
        budget_id=body.budget_id,
        scope=body.scope,
        scope_key=body.scope_key,
        period=body.period,
        limit_usd=body.limit_usd,
        soft_alert_usd=body.soft_alert_usd,
        hard_stop=body.hard_stop,
        enabled=True,
    )
    async with session_scope() as session:
        await BudgetRepository(session).upsert(row)
        await AuditRepository(session).append(
            actor=principal.subject,
            kind="cost.budget.upsert",
            target=body.budget_id,
            diff=body.model_dump(),
            severity="elevated",
        )
    return {"ok": True}


@router.delete("/budgets/{budget_id}")
async def delete_budget(
    budget_id: str, principal: Principal = Depends(require_operator)
) -> dict[str, bool]:
    async with session_scope() as session:
        row = await session.get(BudgetRow, budget_id)
        if row is None:
            raise HTTPException(status_code=404, detail="budget not found")
        await session.delete(row)
        await AuditRepository(session).append(
            actor=principal.subject,
            kind="cost.budget.delete",
            target=budget_id,
            severity="elevated",
        )
    return {"ok": True}
