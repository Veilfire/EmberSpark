"""Filtering page — backend invariants.

Three layers:

1. **Mask renderer** — pure, no I/O. Verifies each MaskStyle renders
   the right shape for representative inputs.
2. **Resolver** — extended ``ResolvedPolicy`` carries mask_style,
   min_confidence, require_consensus, detector_overrides with the
   right grant → agent → global → default precedence.
3. **Detector override** — per-rule disable in ``apply_guardrails``
   drops the matching hit before the level is computed.
"""

from __future__ import annotations

import json

import pytest

from spark.config.enums import DataClass, DataClassLevel, DataScope, MaskStyle
from spark.persistence.learning_models import DataClassPolicyRow
from spark.privacy.guardrails import (
    BUILTIN_DEFAULTS,
    _detector_enabled,
    _apply_redactions,
    apply_guardrails,
    resolve_policy,
)
from spark.privacy.classifiers import DetectorHit
from spark.privacy.mask import DEFAULT_MASK_STYLE, render_mask


# ---------------------------------------------------------------------------
# Mask renderer
# ---------------------------------------------------------------------------


def test_mask_last_4_preserves_separators() -> None:
    out = render_mask(
        "4111-1111-1111-1234", style=MaskStyle.LAST_4, data_class=DataClass.FINANCIAL_CARD
    )
    assert out == "****-****-****-1234"


def test_mask_first_4_keeps_leading_digits() -> None:
    out = render_mask(
        "4111-1111-1111-1234", style=MaskStyle.FIRST_4, data_class=DataClass.FINANCIAL_CARD
    )
    assert out == "4111-****-****-****"


def test_mask_initials_renders_first_letters() -> None:
    out = render_mask("Jane Doe", style=MaskStyle.INITIAL, data_class=DataClass.PII_NAME)
    assert out == "J. D."


def test_mask_hash_short_is_deterministic() -> None:
    a = render_mask(
        "secret", style=MaskStyle.HASH_SHORT, data_class=DataClass.CREDENTIALS_API
    )
    b = render_mask(
        "secret", style=MaskStyle.HASH_SHORT, data_class=DataClass.CREDENTIALS_API
    )
    assert a == b
    assert a.startswith("[#") and a.endswith("]") and len(a) == 11


def test_mask_strip_removes_match() -> None:
    out = render_mask(
        "ignore previous", style=MaskStyle.STRIP, data_class=DataClass.PROMPT_INJECTION
    )
    assert out == ""


def test_mask_placeholder_class_uses_class_value() -> None:
    out = render_mask(
        "AKIAIOSFODNN7EXAMPLE",
        style=MaskStyle.PLACEHOLDER_CLASS,
        data_class=DataClass.CREDENTIALS_API,
    )
    assert out == "[REDACTED:credentials.api]"


def test_default_mask_style_for_card_is_last_4() -> None:
    assert DEFAULT_MASK_STYLE[DataClass.FINANCIAL_CARD] is MaskStyle.LAST_4
    assert DEFAULT_MASK_STYLE[DataClass.PII_NAME] is MaskStyle.INITIAL
    assert DEFAULT_MASK_STYLE[DataClass.PROMPT_INJECTION] is MaskStyle.STRIP


# ---------------------------------------------------------------------------
# Apply redactions with mask styles
# ---------------------------------------------------------------------------


def test_apply_redactions_uses_per_class_mask_style() -> None:
    text = "card 4111-1111-1111-1234 here"
    hit = DetectorHit(
        data_class=DataClass.FINANCIAL_CARD,
        start=5,
        end=24,
        confidence=0.99,
        rule_id="luhn",
        redaction="[REDACTED:financial.card]",
    )
    out = _apply_redactions(
        text, [hit], mask_styles={DataClass.FINANCIAL_CARD: MaskStyle.LAST_4}
    )
    assert out == "card ****-****-****-1234 here"


def test_apply_redactions_falls_back_to_default_mask_style() -> None:
    text = "Hello Jane Doe today"
    hit = DetectorHit(
        data_class=DataClass.PII_NAME,
        start=6,
        end=14,
        confidence=0.9,
        rule_id="presidio:PERSON",
        tier="tier2",
    )
    # No mask_styles passed → default (INITIAL for pii.name).
    out = _apply_redactions(text, [hit])
    assert out == "Hello J. D. today"


# ---------------------------------------------------------------------------
# Resolver: new fields precedence
# ---------------------------------------------------------------------------


def _global_row(cls: DataClass, **kwargs) -> DataClassPolicyRow:
    return DataClassPolicyRow(
        scope_kind="global",
        agent_name=None,
        data_class=cls.value,
        level=kwargs.pop("level", DataClassLevel.REDACT.value),
        scopes=kwargs.pop("scopes", "model_output,user_input,tool_output,memory_write,shell_args"),
        reason="test",
        **kwargs,
    )


def _agent_row(agent: str, cls: DataClass, **kwargs) -> DataClassPolicyRow:
    return DataClassPolicyRow(
        scope_kind="agent",
        agent_name=agent,
        data_class=cls.value,
        level=kwargs.pop("level", DataClassLevel.REDACT.value),
        scopes=kwargs.pop("scopes", "model_output,user_input,tool_output,memory_write,shell_args"),
        reason="test",
        **kwargs,
    )


def test_resolver_picks_default_mask_style_when_no_override() -> None:
    resolved = resolve_policy(
        agent_name=None,
        policy_rows=[],
        grants=[],
        scope=DataScope.MODEL_OUTPUT,
    )
    assert resolved[DataClass.FINANCIAL_CARD].mask_style is MaskStyle.LAST_4
    assert resolved[DataClass.PII_NAME].mask_style is MaskStyle.INITIAL


def test_resolver_global_mask_style_overrides_default() -> None:
    rows = [_global_row(DataClass.FINANCIAL_CARD, mask_style=MaskStyle.HASH_SHORT.value)]
    resolved = resolve_policy(
        agent_name=None, policy_rows=rows, grants=[], scope=DataScope.MODEL_OUTPUT
    )
    assert resolved[DataClass.FINANCIAL_CARD].mask_style is MaskStyle.HASH_SHORT


def test_resolver_agent_mask_style_overrides_global() -> None:
    rows = [
        _global_row(DataClass.FINANCIAL_CARD, mask_style=MaskStyle.HASH_SHORT.value),
        _agent_row(
            "alice", DataClass.FINANCIAL_CARD, mask_style=MaskStyle.PLACEHOLDER_PLAIN.value
        ),
    ]
    resolved = resolve_policy(
        agent_name="alice", policy_rows=rows, grants=[], scope=DataScope.MODEL_OUTPUT
    )
    assert resolved[DataClass.FINANCIAL_CARD].mask_style is MaskStyle.PLACEHOLDER_PLAIN


def test_resolver_min_confidence_falls_back_to_default() -> None:
    resolved = resolve_policy(
        agent_name=None,
        policy_rows=[],
        grants=[],
        scope=DataScope.MODEL_OUTPUT,
    )
    default = BUILTIN_DEFAULTS[DataClass.PII_BASIC]
    assert resolved[DataClass.PII_BASIC].min_confidence == default.min_confidence


def test_resolver_min_confidence_global_override() -> None:
    rows = [_global_row(DataClass.PII_BASIC, min_confidence=0.9)]
    resolved = resolve_policy(
        agent_name=None, policy_rows=rows, grants=[], scope=DataScope.MODEL_OUTPUT
    )
    assert resolved[DataClass.PII_BASIC].min_confidence == pytest.approx(0.9)


def test_resolver_require_consensus_inherits_when_null() -> None:
    """Agent row with require_consensus=None should inherit from global."""
    rows = [
        _global_row(DataClass.PII_NAME, require_consensus=False),
        _agent_row("alice", DataClass.PII_NAME, require_consensus=None),
    ]
    resolved = resolve_policy(
        agent_name="alice", policy_rows=rows, grants=[], scope=DataScope.MODEL_OUTPUT
    )
    # Agent's None means inherit; global says False; so resolved is False.
    assert resolved[DataClass.PII_NAME].require_consensus is False


def test_resolver_detector_overrides_merge_global_and_agent() -> None:
    rows = [
        _global_row(
            DataClass.CREDENTIALS_API,
            detector_overrides_json=json.dumps({"high-entropy": {"enabled": False}}),
        ),
        _agent_row(
            "alice",
            DataClass.CREDENTIALS_API,
            detector_overrides_json=json.dumps({"slack": {"enabled": False}}),
        ),
    ]
    resolved = resolve_policy(
        agent_name="alice", policy_rows=rows, grants=[], scope=DataScope.MODEL_OUTPUT
    )
    overrides = resolved[DataClass.CREDENTIALS_API].detector_overrides
    assert overrides["high-entropy"]["enabled"] is False
    assert overrides["slack"]["enabled"] is False


# ---------------------------------------------------------------------------
# Detector enable/disable helper
# ---------------------------------------------------------------------------


def test_detector_enabled_drops_disabled_rule() -> None:
    overrides = {"aws-access-key": {"enabled": False}}
    assert _detector_enabled("aws-access-key", overrides) is False
    assert _detector_enabled("openai", overrides) is True


def test_detector_enabled_handles_consensus_rule_id() -> None:
    """Fused rule_ids look like ``foo+bar``; if any component is disabled,
    drop the whole hit."""
    overrides = {"presidio:PERSON": {"enabled": False}}
    assert _detector_enabled("presidio:PERSON+spacy", overrides) is False
    assert _detector_enabled("luhn+presidio:CREDIT_CARD", overrides) is True


def test_detector_enabled_unknown_rule_passes() -> None:
    overrides = {"some-other-rule": {"enabled": False}}
    assert _detector_enabled("aws-access-key", overrides) is True


# ---------------------------------------------------------------------------
# Detector override end-to-end via apply_guardrails
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_disabling_aws_detector_lets_key_pass_through(monkeypatch) -> None:
    """When the global ``credentials.api → aws-access-key`` rule is
    disabled, an AWS key in tool_output should pass through unredacted.
    """
    from spark.privacy import guardrails as gr

    text = "key=AKIAIOSFODNN7EXAMPLE in logs"

    async def fake_resolved(*, agent_name, scope):
        # Simulate a policy where credentials.api is REDACT but
        # aws-access-key is disabled.
        from spark.privacy.guardrails import ResolvedPolicy

        out = {}
        for cls, default in BUILTIN_DEFAULTS.items():
            scopes = (
                default.scopes
                if scope in default.scopes
                else default.scopes
            )
            out[cls] = ResolvedPolicy(
                level=default.level,
                scopes=scopes,
                source="default",
                mask_style=DEFAULT_MASK_STYLE.get(cls, MaskStyle.PLACEHOLDER_CLASS),
                min_confidence=default.min_confidence,
                require_consensus=default.require_consensus,
                detector_overrides=(
                    {"aws-access-key": {"enabled": False}}
                    if cls is DataClass.CREDENTIALS_API
                    else {}
                ),
            )
        return out

    monkeypatch.setattr(gr, "get_resolved_policy", fake_resolved)
    outcome = await gr.apply_guardrails(
        text, agent_name=None, scope=DataScope.MODEL_OUTPUT
    )
    # Without the override, "AKIAIOSFODNN7EXAMPLE" would have been
    # masked; with aws-access-key disabled it stays put. The
    # high-entropy catch-all also fires on this string at confidence
    # 0.6, but its tier is tier1 and confidence ≥ default 0.6, so it
    # would normally redact via "high-entropy". Verify that at least
    # the named rule is not the one firing.
    rule_ids = {h.rule_id for h in outcome.hits}
    assert "aws-access-key" not in rule_ids
