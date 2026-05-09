"""Model pricing table.

Values are USD per 1M tokens as of 2026-04. This table is deliberately
in-repo so pricing changes are tracked in git. Operators can override it at
runtime via the web UI (writes to a JSON file under ``~/.spark/pricing.json``).

Five token classes are billed independently:

- ``prompt`` — fresh input tokens (not cached, not just-cached)
- ``cache_read`` — prompt-cache hits (Anthropic ~10% of prompt; OpenAI ~50% of prompt)
- ``cache_creation`` — prompt-cache writes (Anthropic ~125% of prompt; OpenAI same as prompt)
- ``completion`` — output tokens (excluding reasoning)
- ``reasoning`` — o-series / extended-thinking output tokens (billed at completion rate today)

Providers that don't expose every class default the missing classes to the
prompt / completion rate, so legacy ``estimate_cost`` callers keep getting the
same answer.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelPricing:
    provider: str
    model: str
    prompt_per_mtok_usd: float
    completion_per_mtok_usd: float
    cache_read_per_mtok_usd: float | None = None       # default → prompt rate
    cache_creation_per_mtok_usd: float | None = None   # default → prompt rate
    reasoning_per_mtok_usd: float | None = None        # default → completion rate

    def cache_read(self) -> float:
        return self.cache_read_per_mtok_usd if self.cache_read_per_mtok_usd is not None else self.prompt_per_mtok_usd

    def cache_creation(self) -> float:
        return self.cache_creation_per_mtok_usd if self.cache_creation_per_mtok_usd is not None else self.prompt_per_mtok_usd

    def reasoning(self) -> float:
        return self.reasoning_per_mtok_usd if self.reasoning_per_mtok_usd is not None else self.completion_per_mtok_usd


# Anthropic prompt cache: cache_read = 10% of prompt, cache_creation (5m) = 125% of prompt.
# OpenAI prompt cache: cache_read = 50% of prompt; no separate cache_creation charge.
PRICING_TABLE: dict[tuple[str, str], ModelPricing] = {
    ("openai", "gpt-4.1"): ModelPricing(
        "openai", "gpt-4.1", 2.50, 10.00,
        cache_read_per_mtok_usd=1.25,
    ),
    ("openai", "gpt-4.1-mini"): ModelPricing(
        "openai", "gpt-4.1-mini", 0.15, 0.60,
        cache_read_per_mtok_usd=0.075,
    ),
    ("openai", "gpt-4o"): ModelPricing(
        "openai", "gpt-4o", 2.50, 10.00,
        cache_read_per_mtok_usd=1.25,
    ),
    ("openai", "o1"): ModelPricing(
        "openai", "o1", 15.00, 60.00,
        cache_read_per_mtok_usd=7.50,
        reasoning_per_mtok_usd=60.00,
    ),
    ("anthropic", "claude-opus-4-6"): ModelPricing(
        "anthropic", "claude-opus-4-6", 15.00, 75.00,
        cache_read_per_mtok_usd=1.50,        # 10%
        cache_creation_per_mtok_usd=18.75,   # 125%
    ),
    ("anthropic", "claude-sonnet-4-6"): ModelPricing(
        "anthropic", "claude-sonnet-4-6", 3.00, 15.00,
        cache_read_per_mtok_usd=0.30,
        cache_creation_per_mtok_usd=3.75,
    ),
    ("anthropic", "claude-haiku-4-5"): ModelPricing(
        "anthropic", "claude-haiku-4-5", 1.00, 5.00,
        cache_read_per_mtok_usd=0.10,
        cache_creation_per_mtok_usd=1.25,
    ),
    # OpenRouter wildcards default to zero so the local computed value is
    # only ever a fallback. Real cost lands via the deferred enrichment
    # hitting GET /api/v1/generation, which sets cost_source=reported.
    ("openrouter", "*"): ModelPricing("openrouter", "*", 0.0, 0.0),
    ("ollama", "*"): ModelPricing("ollama", "*", 0.0, 0.0),
}


def get_pricing(provider: str, model: str) -> ModelPricing | None:
    """Look up pricing with provider-wildcard fallback."""
    return PRICING_TABLE.get((provider, model)) or PRICING_TABLE.get((provider, "*"))


def estimate_cost(
    *,
    provider: str,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
) -> tuple[float, float, float]:
    """Return (prompt_cost_usd, completion_cost_usd, total_cost_usd).

    Legacy two-bucket interface kept for the run-aggregate path. New code
    should prefer ``compute_cost`` which models cache + reasoning separately.
    """
    pricing = get_pricing(provider, model)
    if pricing is None:
        return 0.0, 0.0, 0.0
    prompt_cost = prompt_tokens / 1_000_000 * pricing.prompt_per_mtok_usd
    completion_cost = completion_tokens / 1_000_000 * pricing.completion_per_mtok_usd
    return prompt_cost, completion_cost, prompt_cost + completion_cost


def compute_cost(
    *,
    provider: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cached_input_tokens: int = 0,
    cache_creation_tokens: int = 0,
    reasoning_tokens: int = 0,
) -> float | None:
    """Compute USD cost from a five-bucket usage breakdown.

    Returns ``None`` when no pricing is configured for the model — callers
    should treat that as "unknown" rather than zero so the UI can render an
    em-dash instead of $0.00.

    The ``cached_input_tokens`` and ``cache_creation_tokens`` are subtracted
    from ``input_tokens`` before applying the prompt rate so the same token
    isn't billed twice. ``reasoning_tokens`` is similarly subtracted from
    ``output_tokens``.
    """
    pricing = get_pricing(provider, model)
    if pricing is None:
        return None
    fresh_input = max(input_tokens - cached_input_tokens - cache_creation_tokens, 0)
    fresh_output = max(output_tokens - reasoning_tokens, 0)
    total = (
        fresh_input * pricing.prompt_per_mtok_usd
        + cached_input_tokens * pricing.cache_read()
        + cache_creation_tokens * pricing.cache_creation()
        + fresh_output * pricing.completion_per_mtok_usd
        + reasoning_tokens * pricing.reasoning()
    ) / 1_000_000
    return total
