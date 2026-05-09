"""Tests for the Thompson-sampling bandit."""

from __future__ import annotations

from spark.learning.bandit import (
    BanditScore,
    select_playbook,
    update_beta_posterior,
)


def test_beta_update_success_increments_alpha() -> None:
    a, b = update_beta_posterior(1.0, 1.0, success=True)
    assert a == 2.0 and b == 1.0


def test_beta_update_failure_increments_beta() -> None:
    a, b = update_beta_posterior(2.0, 1.0, success=False)
    assert a == 2.0 and b == 2.0


def test_empty_candidate_list_returns_none() -> None:
    assert select_playbook([]) is None


def test_bandit_prefers_high_success_rate_playbook() -> None:
    winners: dict[str, int] = {"good": 0, "bad": 0}
    good = BanditScore("good", 0.0, alpha=100.0, beta=1.0, uses=100, applicability=1.0)
    bad = BanditScore("bad", 0.0, alpha=1.0, beta=100.0, uses=100, applicability=1.0)
    for i in range(200):
        choice = select_playbook([good, bad], seed=i)
        if choice is not None:
            winners[choice.playbook_id] += 1
    assert winners["good"] > winners["bad"] * 5


def test_epsilon_exploration_occurs() -> None:
    scores = [
        BanditScore("a", 0.0, alpha=100.0, beta=1.0, uses=100, applicability=1.0),
        BanditScore("b", 0.0, alpha=1.0, beta=100.0, uses=100, applicability=1.0),
    ]
    picks = {select_playbook(scores, epsilon=1.0, seed=i).playbook_id for i in range(50)}  # type: ignore[union-attr]
    # With ε=1.0 we always explore → both candidates can be picked.
    assert "a" in picks and "b" in picks
