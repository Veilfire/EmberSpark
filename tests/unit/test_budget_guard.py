"""Tests for the BudgetGuard in spark.plugins.tool_runtime."""

from __future__ import annotations

import pytest

from spark.plugins.base import BudgetExceeded
from spark.plugins.tool_runtime import BudgetGuard


def test_iterations_budget() -> None:
    guard = BudgetGuard(max_tool_calls=5, max_model_calls=5, max_iterations=2)
    guard.tick_iter()
    guard.tick_iter()
    with pytest.raises(BudgetExceeded):
        guard.tick_iter()


def test_tool_calls_budget() -> None:
    guard = BudgetGuard(max_tool_calls=1, max_model_calls=5, max_iterations=5)
    guard.tick_tool()
    with pytest.raises(BudgetExceeded):
        guard.tick_tool()


def test_model_calls_budget() -> None:
    guard = BudgetGuard(max_tool_calls=5, max_model_calls=1, max_iterations=5)
    guard.tick_model()
    with pytest.raises(BudgetExceeded):
        guard.tick_model()


def test_token_budget_unbounded_by_default() -> None:
    guard = BudgetGuard(max_tool_calls=5, max_model_calls=5, max_iterations=5)
    # No max_tokens_per_run set → tick_tokens is a no-op.
    for _ in range(10_000):
        guard.tick_tokens(1_000)
    assert guard.tokens_used == 0  # counter only ticks when cap is set


def test_token_budget_enforced_when_cap_set() -> None:
    guard = BudgetGuard(
        max_tool_calls=5,
        max_model_calls=5,
        max_iterations=5,
        max_tokens_per_run=250,
    )
    guard.tick_tokens(100)
    guard.tick_tokens(100)
    # Third call crosses the cap.
    with pytest.raises(BudgetExceeded) as exc_info:
        guard.tick_tokens(100)
    assert "tokens budget exceeded" in str(exc_info.value)
    assert exc_info.value.detail["used"] == 300
    assert exc_info.value.detail["limit"] == 250


def test_token_budget_ignores_zero_or_negative() -> None:
    guard = BudgetGuard(
        max_tool_calls=5,
        max_model_calls=5,
        max_iterations=5,
        max_tokens_per_run=100,
    )
    guard.tick_tokens(0)
    guard.tick_tokens(-5)
    assert guard.tokens_used == 0
