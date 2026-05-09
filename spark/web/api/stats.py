"""Agent stats aggregation (one agent, no per-agent breakdown)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import select

from spark.persistence.db import session_scope
from spark.persistence.learning_models import (
    CostEventRow,
    SkillRow,
)
from spark.persistence.models import LongTermMemoryIndexRow, TaskRunRow
from spark.web.auth import Principal, require_viewer

router = APIRouter()


def _p50_p95(values: list[float]) -> tuple[float, float]:
    if not values:
        return 0.0, 0.0
    s = sorted(values)
    p50 = s[len(s) // 2]
    p95 = s[int(len(s) * 0.95)] if len(s) > 1 else s[-1]
    return p50, p95


@router.get("/")
async def get_agent_stats(_: Principal = Depends(require_viewer)) -> dict[str, object]:
    """Aggregate stats for the one agent."""
    now = datetime.now(tz=timezone.utc)
    week_ago = now - timedelta(days=7)

    async with session_scope() as session:
        # Run outcomes
        runs_result = await session.execute(
            select(TaskRunRow).where(TaskRunRow.started_at >= week_ago)
        )
        runs = list(runs_result.scalars().all())

        # Cost
        cost_result = await session.execute(
            select(CostEventRow).where(CostEventRow.recorded_at >= week_ago)
        )
        costs = list(cost_result.scalars().all())

        # Memory writes (long-term index rows created this week)
        mem_result = await session.execute(
            select(LongTermMemoryIndexRow).where(
                LongTermMemoryIndexRow.created_at >= week_ago
            )
        )
        mems = list(mem_result.scalars().all())

        # Skill approvals this week
        skill_result = await session.execute(
            select(SkillRow).where(SkillRow.approved_at >= week_ago)
        )
        skills = list(skill_result.scalars().all())

    total_runs = len(runs)
    completed = [r for r in runs if r.state == "completed"]
    failed = [r for r in runs if r.state == "failed"]
    success_rate = (len(completed) / total_runs) if total_runs else 0.0

    durations: list[float] = []
    for r in runs:
        if r.finished_at is not None:
            delta = (r.finished_at - r.started_at).total_seconds()
            if delta >= 0:
                durations.append(delta)
    p50, p95 = _p50_p95(durations)

    total_cost = sum(c.total_cost_usd for c in costs)
    avg_cost_per_run = total_cost / total_runs if total_runs else 0.0

    return {
        "window_days": 7,
        "runs_total": total_runs,
        "runs_completed": len(completed),
        "runs_failed": len(failed),
        "success_rate": round(success_rate, 3),
        "wall_time_p50_s": round(p50, 3),
        "wall_time_p95_s": round(p95, 3),
        "total_cost_usd": round(total_cost, 4),
        "avg_cost_per_run_usd": round(avg_cost_per_run, 4),
        "memory_writes": len(mems),
        "skills_approved": len(skills),
    }
