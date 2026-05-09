"""Unit tests for the guardrail policy resolver.

Covers precedence (grant → agent → global → default), scope filtering,
and the pure-function invariants that the resolver relies on for
caching correctness.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from spark.config.enums import DataClass, DataClassLevel, DataScope
from spark.persistence.learning_models import (
    DataClassGrantRow,
    DataClassPolicyRow,
)
from spark.privacy.guardrails import BUILTIN_DEFAULTS, resolve_policy


def _row_global(cls: DataClass, level: DataClassLevel, scopes: str) -> DataClassPolicyRow:
    return DataClassPolicyRow(
        scope_kind="global",
        agent_name=None,
        data_class=cls.value,
        level=level.value,
        scopes=scopes,
        reason="test",
    )


def _row_agent(
    agent: str, cls: DataClass, level: DataClassLevel, scopes: str
) -> DataClassPolicyRow:
    return DataClassPolicyRow(
        scope_kind="agent",
        agent_name=agent,
        data_class=cls.value,
        level=level.value,
        scopes=scopes,
        reason="test",
    )


def _grant(
    agent: str,
    cls: DataClass,
    scopes: str,
    override: DataClassLevel = DataClassLevel.ALLOW,
    grant_id: int = 1,
) -> DataClassGrantRow:
    return DataClassGrantRow(
        id=grant_id,
        agent_name=agent,
        data_class=cls.value,
        scopes=scopes,
        level_override=override.value,
        reason="test",
        granted_by="tester",
        granted_at=datetime.now(tz=timezone.utc),
        expires_at=datetime.now(tz=timezone.utc) + timedelta(hours=1),
        active=True,
    )


def test_default_applies_when_no_rows() -> None:
    resolved = resolve_policy(
        agent_name="a1",
        policy_rows=[],
        grants=[],
        scope=DataScope.TOOL_OUTPUT,
    )
    # financial.card defaults to BLOCK.
    assert resolved[DataClass.FINANCIAL_CARD].level is DataClassLevel.BLOCK
    assert resolved[DataClass.FINANCIAL_CARD].source == "default"


def test_global_overrides_default() -> None:
    rows = [
        _row_global(DataClass.FINANCIAL_CARD, DataClassLevel.REDACT, "tool_output"),
    ]
    resolved = resolve_policy(
        agent_name="a1",
        policy_rows=rows,
        grants=[],
        scope=DataScope.TOOL_OUTPUT,
    )
    assert resolved[DataClass.FINANCIAL_CARD].level is DataClassLevel.REDACT
    assert resolved[DataClass.FINANCIAL_CARD].source == "global"


def test_agent_overrides_global() -> None:
    rows = [
        _row_global(DataClass.FINANCIAL_CARD, DataClassLevel.BLOCK, "tool_output"),
        _row_agent("a1", DataClass.FINANCIAL_CARD, DataClassLevel.WARN, "tool_output"),
    ]
    resolved = resolve_policy(
        agent_name="a1",
        policy_rows=rows,
        grants=[],
        scope=DataScope.TOOL_OUTPUT,
    )
    assert resolved[DataClass.FINANCIAL_CARD].level is DataClassLevel.WARN
    assert resolved[DataClass.FINANCIAL_CARD].source == "agent"


def test_grant_beats_everything() -> None:
    rows = [
        _row_global(DataClass.FINANCIAL_CARD, DataClassLevel.BLOCK, "tool_output"),
        _row_agent(
            "cc-agent",
            DataClass.FINANCIAL_CARD,
            DataClassLevel.BLOCK,
            "tool_output",
        ),
    ]
    grants = [
        _grant(
            "cc-agent",
            DataClass.FINANCIAL_CARD,
            "tool_output,user_input",
            override=DataClassLevel.ALLOW,
        ),
    ]
    resolved = resolve_policy(
        agent_name="cc-agent",
        policy_rows=rows,
        grants=grants,
        scope=DataScope.TOOL_OUTPUT,
    )
    assert resolved[DataClass.FINANCIAL_CARD].level is DataClassLevel.ALLOW
    assert resolved[DataClass.FINANCIAL_CARD].source == "grant"
    assert resolved[DataClass.FINANCIAL_CARD].grant_id == 1


def test_grant_scope_filtering() -> None:
    """A grant that covers `tool_output` does not leak into `user_input`."""
    rows = [
        _row_global(
            DataClass.FINANCIAL_CARD, DataClassLevel.BLOCK, "tool_output,user_input"
        ),
    ]
    grants = [
        _grant(
            "cc-agent", DataClass.FINANCIAL_CARD, "tool_output",  # only this scope
        ),
    ]
    # Tool output: grant wins
    r1 = resolve_policy(
        agent_name="cc-agent",
        policy_rows=rows,
        grants=grants,
        scope=DataScope.TOOL_OUTPUT,
    )
    assert r1[DataClass.FINANCIAL_CARD].source == "grant"
    # User input: grant doesn't cover it → global block wins
    r2 = resolve_policy(
        agent_name="cc-agent",
        policy_rows=rows,
        grants=grants,
        scope=DataScope.USER_INPUT,
    )
    assert r2[DataClass.FINANCIAL_CARD].source == "global"
    assert r2[DataClass.FINANCIAL_CARD].level is DataClassLevel.BLOCK


def test_global_scope_filtering() -> None:
    """A global row whose scope does not include the query scope falls back."""
    rows = [
        _row_global(DataClass.FINANCIAL_CARD, DataClassLevel.ALLOW, "tool_output"),
    ]
    # Querying user_input: the global doesn't cover that scope, so the
    # built-in default (BLOCK) applies.
    resolved = resolve_policy(
        agent_name="a1",
        policy_rows=rows,
        grants=[],
        scope=DataScope.USER_INPUT,
    )
    assert resolved[DataClass.FINANCIAL_CARD].source == "default"
    assert resolved[DataClass.FINANCIAL_CARD].level is DataClassLevel.BLOCK


def test_agent_policy_wrong_agent_ignored() -> None:
    rows = [
        _row_agent(
            "other-agent", DataClass.FINANCIAL_CARD, DataClassLevel.ALLOW, "tool_output"
        ),
    ]
    resolved = resolve_policy(
        agent_name="my-agent",
        policy_rows=rows,
        grants=[],
        scope=DataScope.TOOL_OUTPUT,
    )
    # Our agent isn't the one with the override — falls back to default.
    assert resolved[DataClass.FINANCIAL_CARD].source == "default"


def test_multiple_grants_newest_wins() -> None:
    grants = [
        _grant(
            "a1",
            DataClass.FINANCIAL_CARD,
            "tool_output",
            override=DataClassLevel.BLOCK,
            grant_id=1,
        ),
        _grant(
            "a1",
            DataClass.FINANCIAL_CARD,
            "tool_output",
            override=DataClassLevel.ALLOW,
            grant_id=7,
        ),
    ]
    resolved = resolve_policy(
        agent_name="a1", policy_rows=[], grants=grants, scope=DataScope.TOOL_OUTPUT
    )
    assert resolved[DataClass.FINANCIAL_CARD].grant_id == 7
    assert resolved[DataClass.FINANCIAL_CARD].level is DataClassLevel.ALLOW


def test_resolver_is_order_independent() -> None:
    """Same rows in different orders must yield the same resolution."""
    rows_a = [
        _row_global(DataClass.FINANCIAL_CARD, DataClassLevel.BLOCK, "tool_output"),
        _row_agent(
            "a1", DataClass.FINANCIAL_CARD, DataClassLevel.WARN, "tool_output"
        ),
    ]
    rows_b = list(reversed(rows_a))
    r1 = resolve_policy(
        agent_name="a1", policy_rows=rows_a, grants=[], scope=DataScope.TOOL_OUTPUT
    )
    r2 = resolve_policy(
        agent_name="a1", policy_rows=rows_b, grants=[], scope=DataScope.TOOL_OUTPUT
    )
    assert r1[DataClass.FINANCIAL_CARD].level == r2[DataClass.FINANCIAL_CARD].level
    assert r1[DataClass.FINANCIAL_CARD].source == r2[DataClass.FINANCIAL_CARD].source


def test_unknown_class_in_policy_skipped() -> None:
    """Policy rows referencing unknown classes are ignored gracefully."""
    row = DataClassPolicyRow(
        scope_kind="global",
        agent_name=None,
        data_class="not.a.real.class",
        level="block",
        scopes="tool_output",
        reason="test",
    )
    resolved = resolve_policy(
        agent_name="a1",
        policy_rows=[row],
        grants=[],
        scope=DataScope.TOOL_OUTPUT,
    )
    # All known built-in classes still resolve to their defaults.
    for cls in BUILTIN_DEFAULTS:
        assert cls in resolved
