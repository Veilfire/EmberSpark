"""propose_skill plugin — validation, dedupe, rate-limit, kind-specific rules.

Tests run against a tmp SQLite (init_db pulls in the SkillReviewRow + AuditLogRow tables)
and exercise the plugin's `execute` method directly with a synthetic
`_InProcessCtx` — that's the path tool_runtime hits at run time, so this is a
realistic exercise of the actual code path.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from spark.persistence.db import dispose, init_db, session_scope
from spark.persistence.learning_models import SkillReviewRow
from spark.persistence.learning_repos import SkillReviewRepository
from spark.plugins.builtins.propose_skill import (
    ProposeSkillArgs,
    ProposeSkillEndpoint,
    ProposeSkillPlugin,
)
from spark.plugins.tool_runtime import _InProcessCtx
from spark.skills.schemas import ApiSkill, SkillKind
from sqlalchemy import select


def _ctx(config: dict | None = None, agent_name: str = "test-agent") -> _InProcessCtx:
    return _InProcessCtx(
        secrets={},
        plugin_config=config or {},
        scratch_path=None,
        deliverables_path=None,
        privacy_mode="strict",
        agent_name=agent_name,
    )


def _behavior_args(name: str = "claim_decomposition", **overrides) -> ProposeSkillArgs:
    base = dict(
        name=name,
        description="Break complex claims into atomic sub-claims for independent verification.",
        rationale="Improves rigor on multi-part claims; reduces conflation errors.",
        kind="behavior",
        examples=[
            "Claim: 'X is illegal because Y' → decompose into legal status + causal link.",
            "Claim: 'Drug Z causes both A and B' → split into two pharmacology sub-claims.",
        ],
        confidence=0.7,
    )
    base.update(overrides)
    return ProposeSkillArgs(**base)


def _api_args(name: str = "github_issues", **overrides) -> ProposeSkillArgs:
    base = dict(
        name=name,
        description="Read GitHub issues for the configured org.",
        rationale="Fact-checking sometimes needs to cite open-source bug reports.",
        kind="api",
        service_name="GitHub Issues API",
        base_url="https://api.github.com",
        auth_method="bearer",
        auth_secret_hint="github_token",
        required_hosts=["api.github.com"],
        required_secrets=["github_token"],
        endpoints=[
            ProposeSkillEndpoint(
                name="list_issues",
                method="GET",
                path="/repos/{owner}/{repo}/issues",
                description="List issues in a repo.",
            ),
        ],
        confidence=0.8,
    )
    base.update(overrides)
    return ProposeSkillArgs(**base)


# ---------------------------------------------------------------------------
# Argument validation
# ---------------------------------------------------------------------------


def test_args_name_must_be_slug() -> None:
    with pytest.raises(Exception):
        ProposeSkillArgs(
            name="Claim Decomposition",  # spaces / capitals — not a slug
            description="x",
            rationale="x",
            kind="behavior",
            examples=["e"],
        )


def test_args_confidence_clamps() -> None:
    a = ProposeSkillArgs(
        name="x",
        description="x",
        rationale="x",
        kind="behavior",
        examples=["e"],
        confidence=99.0,
    )
    assert a.confidence == 1.0
    b = ProposeSkillArgs(
        name="x",
        description="x",
        rationale="x",
        kind="behavior",
        examples=["e"],
        confidence=-0.5,
    )
    assert b.confidence == 0.0


# ---------------------------------------------------------------------------
# kind-specific cross-field rules (enforced inside execute())
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_kind_api_requires_base_url(tmp_path: Path) -> None:
    await init_db(tmp_path / "spark.db")
    try:
        args = ProposeSkillArgs(
            name="x", description="d", rationale="r", kind="api",
            service_name="S",
            # base_url left empty
        )
        with pytest.raises(PermissionError, match="kind=api requires"):
            await ProposeSkillPlugin().execute(args, _ctx())
    finally:
        await dispose()


@pytest.mark.asyncio
async def test_kind_api_rejects_non_http_base_url(tmp_path: Path) -> None:
    await init_db(tmp_path / "spark.db")
    try:
        args = ProposeSkillArgs(
            name="x", description="d", rationale="r", kind="api",
            service_name="S", base_url="ftp://oops.example/api",
        )
        with pytest.raises(PermissionError, match="must start with http"):
            await ProposeSkillPlugin().execute(args, _ctx())
    finally:
        await dispose()


@pytest.mark.asyncio
async def test_kind_behavior_requires_examples(tmp_path: Path) -> None:
    await init_db(tmp_path / "spark.db")
    try:
        args = ProposeSkillArgs(
            name="x", description="d", rationale="r", kind="behavior",
            examples=[],  # empty — should refuse
        )
        with pytest.raises(PermissionError, match="kind=behavior requires"):
            await ProposeSkillPlugin().execute(args, _ctx())
    finally:
        await dispose()


@pytest.mark.asyncio
async def test_kind_knowledge_no_extra_required_fields(tmp_path: Path) -> None:
    await init_db(tmp_path / "spark.db")
    try:
        args = ProposeSkillArgs(
            name="dna_breaks_norms",
            description="DNA codes can deviate from standard rules in pond organisms.",
            rationale="Surfaces a counter-example when reasoning about genetic universality.",
            kind="knowledge",
        )
        result = await ProposeSkillPlugin().execute(args, _ctx())
        assert result.state == "pending"
        assert result.dedupe_action == "created"
    finally:
        await dispose()


# ---------------------------------------------------------------------------
# Disabled / rate-limit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_disabled_refuses_with_clear_error(tmp_path: Path) -> None:
    await init_db(tmp_path / "spark.db")
    try:
        ctx = _ctx(config={"enabled": False})
        with pytest.raises(PermissionError, match="disabled skill proposals"):
            await ProposeSkillPlugin().execute(_behavior_args(), ctx)
    finally:
        await dispose()


@pytest.mark.asyncio
async def test_rate_limit_refuses_at_cap(tmp_path: Path) -> None:
    await init_db(tmp_path / "spark.db")
    try:
        ctx = _ctx(config={"max_pending_per_agent": 3, "cooldown_seconds": 0})
        plugin = ProposeSkillPlugin()
        for i in range(3):
            res = await plugin.execute(_behavior_args(name=f"skill_{i}"), ctx)
            assert res.dedupe_action == "created"
        with pytest.raises(PermissionError, match="cap 3"):
            await plugin.execute(_behavior_args(name="skill_overflow"), ctx)
    finally:
        await dispose()


# ---------------------------------------------------------------------------
# Dedupe / cooldown
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dedupe_reject_default(tmp_path: Path) -> None:
    """Default strategy = reject_duplicate within the cooldown window."""
    await init_db(tmp_path / "spark.db")
    try:
        ctx = _ctx(config={"cooldown_seconds": 600})  # 10-min window
        plugin = ProposeSkillPlugin()
        a = await plugin.execute(_behavior_args(name="dedupe_test"), ctx)
        assert a.dedupe_action == "created"
        with pytest.raises(PermissionError, match="already exists"):
            await plugin.execute(_behavior_args(name="dedupe_test"), ctx)
    finally:
        await dispose()


@pytest.mark.asyncio
async def test_dedupe_update_pending(tmp_path: Path) -> None:
    """update_pending overwrites the existing pending row."""
    await init_db(tmp_path / "spark.db")
    try:
        ctx = _ctx(
            config={"cooldown_seconds": 600, "dedupe_strategy": "update_pending"}
        )
        plugin = ProposeSkillPlugin()
        first = await plugin.execute(
            _behavior_args(name="iter_test", description="v1 desc"), ctx
        )
        second = await plugin.execute(
            _behavior_args(name="iter_test", description="v2 better desc"), ctx
        )
        assert first.review_id == second.review_id
        assert second.dedupe_action == "updated_existing"
        # Check the row actually got updated
        async with session_scope() as session:
            row = await session.get(SkillReviewRow, second.review_id)
            assert row is not None
            assert row.proposed_description == "v2 better desc"
    finally:
        await dispose()


@pytest.mark.asyncio
async def test_dedupe_window_zero_disables_cooldown(tmp_path: Path) -> None:
    """cooldown_seconds=0 means an immediate re-propose creates a new row.

    Note this still requires a *different* name since same-name+same-agent
    pending rows will otherwise dupe — but with cooldown=0 they're not
    treated as a dupe at all. Use this when the agent is iterating fast.
    """
    await init_db(tmp_path / "spark.db")
    try:
        ctx = _ctx(config={"cooldown_seconds": 0})
        plugin = ProposeSkillPlugin()
        a = await plugin.execute(_behavior_args(name="a"), ctx)
        b = await plugin.execute(_behavior_args(name="b"), ctx)
        assert a.review_id != b.review_id
    finally:
        await dispose()


# ---------------------------------------------------------------------------
# Side effects: row contents + audit + payload roundtrip
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_behavior_skill_persists_payload_with_kind_and_rationale(
    tmp_path: Path,
) -> None:
    await init_db(tmp_path / "spark.db")
    try:
        plugin = ProposeSkillPlugin()
        args = _behavior_args(name="bias_detection")
        result = await plugin.execute(args, _ctx())
        assert result.state == "pending"

        async with session_scope() as session:
            row = await session.get(SkillReviewRow, result.review_id)
            assert row is not None
            # Round-trip: payload_json should parse back to ApiSkill with the
            # new fields populated.
            skill = ApiSkill.model_validate_json(row.payload_json)
            assert skill.kind == SkillKind.BEHAVIOR
            assert skill.rationale == args.rationale
            assert skill.examples == args.examples
            # Sentinel base_url for non-API kinds
            assert skill.base_url.startswith("agent-proposal://")
            # namespace flags the agent-proposed source
            assert row.namespace == "agent-proposed/behavior"
    finally:
        await dispose()


@pytest.mark.asyncio
async def test_api_skill_persists_endpoints(tmp_path: Path) -> None:
    await init_db(tmp_path / "spark.db")
    try:
        plugin = ProposeSkillPlugin()
        result = await plugin.execute(_api_args(name="github_x"), _ctx())
        async with session_scope() as session:
            row = await session.get(SkillReviewRow, result.review_id)
            skill = ApiSkill.model_validate_json(row.payload_json)
            assert skill.kind == SkillKind.API
            assert skill.base_url == "https://api.github.com"
            assert len(skill.endpoints) == 1
            assert skill.endpoints[0].name == "list_issues"
    finally:
        await dispose()


@pytest.mark.asyncio
async def test_pending_count_in_result_matches_db(tmp_path: Path) -> None:
    await init_db(tmp_path / "spark.db")
    try:
        ctx = _ctx(config={"cooldown_seconds": 0})
        plugin = ProposeSkillPlugin()
        r1 = await plugin.execute(_behavior_args(name="a"), ctx)
        r2 = await plugin.execute(_behavior_args(name="b"), ctx)
        r3 = await plugin.execute(_behavior_args(name="c"), ctx)
        assert (r1.pending_count, r2.pending_count, r3.pending_count) == (1, 2, 3)
        async with session_scope() as session:
            stmt = select(SkillReviewRow).where(
                SkillReviewRow.agent_name == "test-agent",
                SkillReviewRow.state == "pending",
            )
            rows = list((await session.execute(stmt)).scalars().all())
            assert len(rows) == 3
    finally:
        await dispose()


# ---------------------------------------------------------------------------
# Schema backward compat
# ---------------------------------------------------------------------------


def test_legacy_apiskill_payload_loads_with_default_kind() -> None:
    """A pre-extension ApiSkill payload (no kind/rationale fields) parses
    back as kind=api with empty rationale/examples — so existing
    discovery-flow rows in skill_reviews keep working after the schema
    extension.
    """
    legacy = '{"name":"x","description":"d","service_name":"s","base_url":"https://x.com","auth_method":"none","required_hosts":[],"required_secrets":[],"endpoints":[],"pricing_notes":"","rate_limit_notes":"","source_url":"https://x/docs","confidence":0.5}'
    skill = ApiSkill.model_validate_json(legacy)
    assert skill.kind == SkillKind.API
    assert skill.rationale == ""
    assert skill.examples == []
