"""Tests for the cost tracker + budget enforcement."""

from __future__ import annotations

from pathlib import Path

import pytest

from spark.cost.pricing import compute_cost, estimate_cost, get_pricing
from spark.cost.tracker import (
    BudgetExceeded,
    CostTracker,
    check_budgets,
    record_usage,
)
from spark.persistence.db import dispose, init_db, session_scope
from spark.persistence.learning_models import (
    BudgetRow,
    CostEventRow,
    ModelCallEventRow,
)
from spark.persistence.learning_repos import (
    BudgetRepository,
    CostRepository,
    ModelCallEventRepository,
)
from sqlalchemy import select


def test_estimate_cost_openai_gpt41() -> None:
    p, c, total = estimate_cost(
        provider="openai", model="gpt-4.1", prompt_tokens=1_000_000, completion_tokens=500_000
    )
    assert p == 2.50
    assert c == 5.0
    assert round(total, 2) == 7.50


def test_estimate_cost_ollama_free() -> None:
    p, c, total = estimate_cost(
        provider="ollama", model="llama3.1", prompt_tokens=10_000, completion_tokens=10_000
    )
    assert total == 0.0


def test_cost_tracker_accumulates() -> None:
    tracker = CostTracker(
        run_id="r1",
        agent_name="a",
        task_name="t",
        provider="openai",
        model="gpt-4.1",
    )
    tracker.add(1000, 500)
    tracker.add(2000, 1000)
    assert tracker.prompt_tokens == 3000
    assert tracker.completion_tokens == 1500
    assert tracker.total_cost_usd > 0


@pytest.mark.asyncio
async def test_budget_enforcement_blocks_when_exceeded(tmp_path: Path):
    await init_db(tmp_path / "spark.db")
    try:
        async with session_scope() as session:
            repo = BudgetRepository(session)
            await repo.upsert(
                BudgetRow(
                    budget_id="daily-alpha",
                    scope="agent",
                    scope_key="alpha",
                    period="daily",
                    limit_usd=0.0001,  # intentionally tiny
                    hard_stop=True,
                    enabled=True,
                )
            )
        tracker = CostTracker(
            run_id="r1",
            agent_name="alpha",
            task_name="t",
            provider="openai",
            model="gpt-4.1",
        )
        tracker.add(100_000, 100_000)
        await record_usage(tracker)
        with pytest.raises(BudgetExceeded):
            await check_budgets(agent_name="alpha", provider="openai")
    finally:
        await dispose()


@pytest.mark.asyncio
async def test_budget_not_enforced_for_other_scope(tmp_path: Path):
    await init_db(tmp_path / "spark.db")
    try:
        async with session_scope() as session:
            repo = BudgetRepository(session)
            await repo.upsert(
                BudgetRow(
                    budget_id="daily-beta",
                    scope="agent",
                    scope_key="beta",
                    period="daily",
                    limit_usd=0.0001,
                    hard_stop=True,
                )
            )
        tracker = CostTracker(
            run_id="r1",
            agent_name="alpha",
            task_name="t",
            provider="openai",
            model="gpt-4.1",
        )
        tracker.add(100_000, 100_000)
        await record_usage(tracker)
        # alpha's budget is unlimited so this should not raise
        await check_budgets(agent_name="alpha", provider="openai")
    finally:
        await dispose()


# -----------------------------------------------------------------------------
# Per-call cost: cache pricing math + cost_source precedence
# -----------------------------------------------------------------------------


def test_compute_cost_anthropic_cache_discount() -> None:
    """Cache_read tokens cost ~10% of fresh prompt tokens (Anthropic schedule)."""
    fresh_only = compute_cost(
        provider="anthropic",
        model="claude-sonnet-4-6",
        input_tokens=1_000_000,
        output_tokens=0,
    )
    half_cached = compute_cost(
        provider="anthropic",
        model="claude-sonnet-4-6",
        input_tokens=1_000_000,
        output_tokens=0,
        cached_input_tokens=500_000,
    )
    pricing = get_pricing("anthropic", "claude-sonnet-4-6")
    assert pricing is not None
    # Half the prompt tokens were cache-reads → priced at the cache_read rate.
    expected = (
        500_000 * pricing.prompt_per_mtok_usd
        + 500_000 * pricing.cache_read()
    ) / 1_000_000
    assert fresh_only == pytest.approx(pricing.prompt_per_mtok_usd)
    assert half_cached == pytest.approx(expected)
    assert half_cached < fresh_only  # the discount must materialise


def test_compute_cost_anthropic_cache_creation_premium() -> None:
    """Cache_creation tokens cost ~125% of fresh prompt tokens."""
    cost = compute_cost(
        provider="anthropic",
        model="claude-sonnet-4-6",
        input_tokens=1_000_000,
        output_tokens=0,
        cache_creation_tokens=1_000_000,
    )
    pricing = get_pricing("anthropic", "claude-sonnet-4-6")
    assert pricing is not None
    # All input tokens were cache-creation; fresh_input==0.
    assert cost == pytest.approx(pricing.cache_creation())
    assert cost > pricing.prompt_per_mtok_usd  # premium over fresh prompt


def test_compute_cost_unknown_model_returns_none() -> None:
    assert (
        compute_cost(
            provider="openai",
            model="gpt-9999-not-a-real-model",
            input_tokens=100,
            output_tokens=50,
        )
        is None
    )


def test_compute_cost_o1_reasoning_billed_at_completion_rate() -> None:
    """o-series reasoning tokens are billed at the completion rate."""
    no_reasoning = compute_cost(
        provider="openai",
        model="o1",
        input_tokens=100,
        output_tokens=1000,
    )
    with_reasoning = compute_cost(
        provider="openai",
        model="o1",
        input_tokens=100,
        output_tokens=1000,
        reasoning_tokens=500,
    )
    # reasoning is part of output_tokens; the helper subtracts it before
    # applying the completion rate, then adds it back at the reasoning rate
    # (which equals completion rate for o1) — so totals match.
    assert no_reasoning == pytest.approx(with_reasoning)


@pytest.mark.asyncio
async def test_record_usage_aggregates_from_per_call_rows(tmp_path: Path):
    """When per-call rows exist, the run aggregate sums them — and a
    `reported` row's cost (e.g. from OpenRouter enrichment) flows into the
    aggregate intact, not replaced by the local price-table estimate."""
    await init_db(tmp_path / "spark.db")
    try:
        async with session_scope() as session:
            repo = ModelCallEventRepository(session)
            await repo.record(
                ModelCallEventRow(
                    run_id="r-aggregate",
                    sequence=1,
                    provider="openrouter",
                    model="anthropic/claude-sonnet-4-6",
                    request_id="gen-aaa",
                    input_tokens=1000,
                    output_tokens=500,
                    cost_usd=0.012345,
                    cost_source="reported",
                )
            )
            await repo.record(
                ModelCallEventRow(
                    run_id="r-aggregate",
                    sequence=2,
                    provider="openrouter",
                    model="anthropic/claude-sonnet-4-6",
                    request_id="gen-bbb",
                    input_tokens=2000,
                    output_tokens=1000,
                    cost_usd=0.024_690,
                    cost_source="reported",
                )
            )
        tracker = CostTracker(
            run_id="r-aggregate",
            agent_name="a",
            task_name="t",
            provider="openrouter",
            model="anthropic/claude-sonnet-4-6",
        )
        # In-memory accumulator stays at zero — the aggregator should pull
        # from the per-call rows instead.
        await record_usage(tracker)

        async with session_scope() as session:
            result = await session.execute(
                select(CostEventRow).where(CostEventRow.run_id == "r-aggregate")
            )
            rows = list(result.scalars().all())
        assert len(rows) == 1
        agg = rows[0]
        assert agg.total_tokens == 4500
        assert agg.total_cost_usd == pytest.approx(0.012345 + 0.024_690)
    finally:
        await dispose()


@pytest.mark.asyncio
async def test_update_from_enrichment_flips_cost_source(tmp_path: Path):
    """Simulates the OpenRouter post-hoc enrichment landing on a
    `computed` row. The row should flip to `reported` and pick up the
    authoritative USD value."""
    await init_db(tmp_path / "spark.db")
    try:
        async with session_scope() as session:
            repo = ModelCallEventRepository(session)
            row = await repo.record(
                ModelCallEventRow(
                    run_id="r-enrich",
                    sequence=1,
                    provider="openrouter",
                    model="anthropic/claude-sonnet-4-6",
                    request_id="gen-zzz",
                    input_tokens=1000,
                    output_tokens=500,
                    cost_usd=0.0,  # computed default for openrouter wildcard
                    cost_source="computed",
                )
            )
            assert row.id is not None

        async with session_scope() as session:
            await ModelCallEventRepository(session).update_from_enrichment(
                row_id=row.id,
                cost_usd=0.004_734,
                raw_metadata_merge={"usage": 0.004_734, "native_tokens_prompt": 233},
            )

        async with session_scope() as session:
            result = await session.execute(
                select(ModelCallEventRow).where(ModelCallEventRow.id == row.id)
            )
            updated = result.scalars().one()
        assert updated.cost_source == "reported"
        assert updated.cost_usd == pytest.approx(0.004_734)
        assert "openrouter_enriched" in updated.raw_metadata_json
    finally:
        await dispose()
