"""Cost tracker — records per-run token + cost events and enforces budgets."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from spark.cost.pricing import estimate_cost
from spark.persistence.db import session_scope
from spark.persistence.learning_models import CostEventRow
from spark.persistence.learning_repos import BudgetRepository, CostRepository


class BudgetExceeded(RuntimeError):
    pass


@dataclass
class CostTracker:
    """Accumulates per-run token/cost and provides in-memory aggregates."""

    run_id: str
    agent_name: str
    task_name: str | None
    provider: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_cost_usd: float = 0.0
    events: list[dict[str, float]] = field(default_factory=list)

    def add(self, prompt_tokens: int, completion_tokens: int) -> None:
        p_cost, c_cost, total = estimate_cost(
            provider=self.provider,
            model=self.model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )
        self.prompt_tokens += prompt_tokens
        self.completion_tokens += completion_tokens
        self.total_cost_usd += total
        self.events.append(
            {
                "prompt_tokens": float(prompt_tokens),
                "completion_tokens": float(completion_tokens),
                "cost": total,
            }
        )


async def record_usage(tracker: CostTracker) -> None:
    """Persist a single aggregated cost event for the finished run.

    Prefers ``SUM(model_call_events)`` when per-call rows exist (so any
    OpenRouter enrichment that flipped rows to ``cost_source=reported``
    is reflected in the aggregate). Falls back to the in-memory
    accumulator + price table when no per-call rows landed (e.g. a run
    that errored before any model call recorded).
    """
    from spark.persistence.learning_repos import ModelCallEventRepository

    async with session_scope() as session:
        repo = CostRepository(session)
        per_call = await ModelCallEventRepository(session).list_for_run(tracker.run_id)
        if per_call:
            prompt_tokens = sum(r.input_tokens for r in per_call)
            completion_tokens = sum(r.output_tokens for r in per_call)
            total_cost = sum(r.cost_usd or 0.0 for r in per_call)
            # Splitting the aggregate into prompt/completion buckets
            # roughly: weight by the price-table ratio so the legacy
            # columns stay populated for the existing dashboard.
            p_cost, c_cost, _ = estimate_cost(
                provider=tracker.provider,
                model=tracker.model,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
            )
            ratio_total = (p_cost + c_cost) or 1.0
            split_prompt = total_cost * (p_cost / ratio_total)
            split_completion = total_cost - split_prompt
        else:
            prompt_tokens = tracker.prompt_tokens
            completion_tokens = tracker.completion_tokens
            split_prompt, split_completion, total_cost = estimate_cost(
                provider=tracker.provider,
                model=tracker.model,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
            )

        await repo.record(
            CostEventRow(
                run_id=tracker.run_id,
                agent_name=tracker.agent_name,
                task_name=tracker.task_name,
                provider=tracker.provider,
                model=tracker.model,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=prompt_tokens + completion_tokens,
                prompt_cost_usd=split_prompt,
                completion_cost_usd=split_completion,
                total_cost_usd=total_cost,
            )
        )


async def check_budgets(*, agent_name: str, provider: str) -> None:
    """Raise BudgetExceeded if any active budget has blown its hard-stop limit.

    Also fires notifications at the soft-alert and hard-stop thresholds via
    the notification service (subject to per-kind user preferences). Failures
    on the notification path are non-fatal.
    """
    from spark.notifications import NotificationKind, get_notification_service

    svc = get_notification_service()

    async with session_scope() as session:
        budgets = await BudgetRepository(session).list_all()
        cost_repo = CostRepository(session)
        for b in budgets:
            if not b.enabled:
                continue
            if b.scope == "agent" and b.scope_key != agent_name:
                continue
            if b.scope == "provider" and b.scope_key != provider:
                continue
            since = _period_start(b.period)
            spent = await cost_repo.total_usd(
                since=since,
                scope=b.scope,
                scope_key=None if b.scope == "global" else b.scope_key,
            )
            # Soft alert fires on the way up; we only notify the first time
            # the threshold is crossed per period (dedup via target_id).
            if b.soft_alert_usd > 0 and spent >= b.soft_alert_usd:
                await svc.notify(
                    NotificationKind.COST_SOFT_ALERT,
                    title=f"Cost soft-alert tripped: {b.budget_id}",
                    body=(
                        f"Scope {b.scope}:{b.scope_key} spent ${spent:.2f}"
                        f" (soft ${b.soft_alert_usd:.2f}, hard ${b.limit_usd:.2f})"
                    ),
                    severity="info",
                    target_kind="budget",
                    target_id=f"{b.budget_id}:{b.period}:{int(since.timestamp())}",
                    action_url="/cost",
                )
            if b.hard_stop and spent >= b.limit_usd:
                await svc.notify(
                    NotificationKind.COST_HARD_STOP,
                    title=f"Cost hard stop: {b.budget_id}",
                    body=(
                        f"Scope {b.scope}:{b.scope_key} spent ${spent:.2f}"
                        f" / ${b.limit_usd:.2f}. Runs refused until the period resets."
                    ),
                    severity="elevated",
                    target_kind="budget",
                    target_id=f"{b.budget_id}:{b.period}:{int(since.timestamp())}",
                    action_url="/cost",
                )
                raise BudgetExceeded(
                    f"budget {b.budget_id} ({b.scope}:{b.scope_key}) "
                    f"spent ${spent:.2f}/${b.limit_usd:.2f}"
                )


def _period_start(period: str) -> datetime:
    now = datetime.now(tz=UTC)
    if period == "daily":
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    if period == "weekly":
        return (now - timedelta(days=now.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
    if period == "monthly":
        return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return now - timedelta(days=30)
