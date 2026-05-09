"""Tool-output filtering for pre-model exposure.

Combines the redaction pipeline with structural filtering: field suppression,
large-blob truncation, and sensitivity tagging. This is the final gate before a
tool result is handed back to the LangGraph engine to be shown to the model.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from spark.config.enums import PrivacyMode, Sensitivity
from spark.privacy.redaction import RedactionResult, redact_structure
from spark.privacy.sensitivity import decide

MAX_STRING_LEN_STRICT = 4_000
MAX_STRING_LEN_BALANCED = 16_000


@dataclass(frozen=True)
class FilterOutcome:
    content: Any
    sensitivity: Sensitivity
    redactions: tuple[str, ...]
    truncated: bool


def _truncate_strings(obj: Any, limit: int) -> tuple[Any, bool]:
    truncated = False

    def walk(value: Any) -> Any:
        nonlocal truncated
        if isinstance(value, str) and len(value) > limit:
            truncated = True
            return value[: limit - 20] + "...[truncated]"
        if isinstance(value, dict):
            return {k: walk(v) for k, v in value.items()}
        if isinstance(value, list):
            return [walk(v) for v in value]
        if isinstance(value, tuple):
            return tuple(walk(v) for v in value)
        return value

    return walk(obj), truncated


def filter_for_model(
    content: Any,
    *,
    privacy_mode: PrivacyMode,
    declared_sensitivity: Sensitivity,
    drop_fields: frozenset[str] = frozenset(),
) -> FilterOutcome:
    """Filter tool output before model exposure.

    1. Honor the sensitivity gate: refuse model exposure if policy says so.
    2. Drop user-nominated fields.
    3. Redact (regex + entropy + Presidio depending on mode).
    4. Truncate large strings.
    """
    policy = decide(privacy_mode, declared_sensitivity)
    if not policy.allow_model:
        return FilterOutcome(
            content={"error": f"content blocked by privacy policy ({declared_sensitivity.value})"},
            sensitivity=declared_sensitivity,
            redactions=("POLICY_BLOCK",),
            truncated=False,
        )

    filtered: Any = content
    if isinstance(filtered, dict) and drop_fields:
        filtered = {k: v for k, v in filtered.items() if k not in drop_fields}

    use_presidio = privacy_mode != PrivacyMode.REGEX_ONLY
    filtered, applied = redact_structure(filtered, use_presidio=use_presidio)

    limit = MAX_STRING_LEN_STRICT if privacy_mode == PrivacyMode.STRICT else MAX_STRING_LEN_BALANCED
    filtered, truncated = _truncate_strings(filtered, limit)

    return FilterOutcome(
        content=filtered,
        sensitivity=declared_sensitivity,
        redactions=tuple(applied),
        truncated=truncated,
    )


def redact_text_for_log(text: str, privacy_mode: PrivacyMode) -> RedactionResult:
    """Redact a plain-text blob for logging."""
    from spark.privacy.redaction import redact

    use_presidio = privacy_mode != PrivacyMode.REGEX_ONLY
    return redact(text, use_presidio=use_presidio)
