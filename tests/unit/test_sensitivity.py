"""Tests for the sensitivity policy table."""

from __future__ import annotations

from spark.config.enums import PrivacyMode, Sensitivity
from spark.privacy.sensitivity import decide


def test_strict_blocks_restricted_model_exposure() -> None:
    policy = decide(PrivacyMode.STRICT, Sensitivity.RESTRICTED)
    assert policy.allow_model is False
    assert policy.allow_long_term is False
    assert policy.allow_log is False


def test_strict_blocks_high_from_long_term() -> None:
    policy = decide(PrivacyMode.STRICT, Sensitivity.HIGH)
    assert policy.allow_model is True
    assert policy.allow_long_term is False


def test_balanced_allows_high_long_term() -> None:
    policy = decide(PrivacyMode.BALANCED, Sensitivity.HIGH)
    assert policy.allow_long_term is True


def test_low_passes_everything() -> None:
    for mode in (PrivacyMode.STRICT, PrivacyMode.BALANCED):
        policy = decide(mode, Sensitivity.LOW)
        assert policy.allow_model and policy.allow_long_term and policy.allow_log
