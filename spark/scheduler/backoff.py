"""Retry policy + exponential backoff with jitter."""

from __future__ import annotations

import random
from dataclasses import dataclass

from spark.config.models import RetryPolicy


@dataclass(frozen=True)
class BackoffStep:
    attempt: int
    delay_seconds: float


def backoff_delay(policy: RetryPolicy, attempt: int) -> float:
    """Compute a delay for the ``attempt``-th retry (1-indexed).

    Exponential: ``delay = base * multiplier^(attempt-1) + jitter``.
    Jitter is uniform in ``[0, jitter_seconds]``. Delay is capped at 1 hour.
    """
    if attempt < 1:
        return 0.0
    base = policy.backoff_seconds
    mult = policy.backoff_multiplier
    exp = base * (mult ** (attempt - 1))
    jitter = random.SystemRandom().uniform(0, policy.jitter_seconds)
    return min(exp + jitter, 3600.0)


def should_give_up(policy: RetryPolicy, consecutive_failures: int) -> bool:
    return consecutive_failures >= policy.max_attempts
