"""Thompson-sampling multi-armed bandit over playbooks.

Each playbook has a Beta posterior over its success probability. On selection
we draw one sample per candidate and pick the argmax. On outcome we update
alpha/beta. The first-seen playbook starts at Beta(1,1) — a uniform prior.
"""

from __future__ import annotations

import random
from dataclasses import dataclass


@dataclass(frozen=True)
class BanditScore:
    playbook_id: str
    sampled_value: float
    alpha: float
    beta: float
    uses: int
    applicability: float


def update_beta_posterior(alpha: float, beta: float, success: bool) -> tuple[float, float]:
    """Classic Bayesian update for a Beta(alpha, beta) prior over a Bernoulli outcome."""
    if success:
        return alpha + 1.0, beta
    return alpha, beta + 1.0


def _thompson_sample(alpha: float, beta: float, rng: random.Random) -> float:
    """Draw a Thompson sample from Beta(alpha, beta) using betavariate."""
    # rng.betavariate asserts a > 0 and b > 0 — we clamp just in case.
    a = max(alpha, 1e-3)
    b = max(beta, 1e-3)
    return rng.betavariate(a, b)


def select_playbook(
    candidates: list[BanditScore],
    *,
    epsilon: float = 0.05,
    seed: int | None = None,
) -> BanditScore | None:
    """Pick one candidate by Thompson sampling weighted by applicability.

    `epsilon` is an explicit exploration knob: with probability ε we ignore the
    bandit and pick a uniformly random candidate. This keeps stale-but-high-scoring
    playbooks from locking out newer ones.
    """
    if not candidates:
        return None
    rng = random.Random(seed) if seed is not None else random.SystemRandom()

    if rng.random() < epsilon:
        return rng.choice(candidates)

    # Re-score each candidate with a fresh Thompson draw blended with applicability.
    scored: list[tuple[float, BanditScore]] = []
    for c in candidates:
        sample = _thompson_sample(c.alpha, c.beta, rng)
        blended = sample * max(0.1, c.applicability)
        scored.append((blended, c))
    scored.sort(key=lambda t: t[0], reverse=True)
    return scored[0][1]
