"""Tests for SkillCatalog — staging, approval, rejection."""

from __future__ import annotations

from pathlib import Path

import pytest

from spark.persistence.db import dispose, init_db
from spark.skills.catalog import SkillCatalog
from spark.skills.schemas import ApiSkill, SkillAuthMethod, SkillReviewDecision


@pytest.fixture
async def db(tmp_path: Path):
    await init_db(tmp_path / "spark.db")
    yield
    await dispose()


@pytest.fixture
def sample_skill() -> ApiSkill:
    return ApiSkill(
        name="send-telegram",
        description="send a telegram message via bot API",
        service_name="telegram",
        base_url="https://api.telegram.org",
        auth_method=SkillAuthMethod.BEARER,
        auth_secret_hint="telegram_bot_token",
        required_hosts=["api.telegram.org"],
        required_secrets=["telegram_bot_token"],
        source_url="https://core.telegram.org/bots/api",
        confidence=0.85,
    )


@pytest.mark.asyncio
async def test_stage_for_review_goes_pending(db, sample_skill):
    catalog = SkillCatalog()
    pending = await catalog.stage_for_review(
        agent_name="alpha", namespace="ns", skill=sample_skill
    )
    assert pending.state == "pending"
    assert pending.skill.name == "send-telegram"

    queue = await catalog.list_pending()
    assert len(queue) == 1
    assert queue[0].review_id == pending.review_id


@pytest.mark.asyncio
async def test_approve_creates_skill_row(db, sample_skill):
    catalog = SkillCatalog()
    pending = await catalog.stage_for_review(
        agent_name="alpha", namespace="ns", skill=sample_skill
    )
    result = await catalog.decide(
        SkillReviewDecision(
            review_id=pending.review_id,
            decision="approve",
            reviewer="test-user",
        )
    )
    assert result is not None
    assert result.state == "approved"

    # Skill row should exist now
    rows = await catalog.list_approved_for_agent("alpha")
    assert len(rows) == 1
    assert rows[0].service_name == "telegram"
    assert rows[0].approved_by == "test-user"


@pytest.mark.asyncio
async def test_reject_does_not_create_skill(db, sample_skill):
    catalog = SkillCatalog()
    pending = await catalog.stage_for_review(
        agent_name="alpha", namespace="ns", skill=sample_skill
    )
    result = await catalog.decide(
        SkillReviewDecision(
            review_id=pending.review_id,
            decision="reject",
            reviewer="test-user",
            notes="off-policy",
        )
    )
    assert result is not None
    assert result.state == "rejected"

    rows = await catalog.list_approved_for_agent("alpha")
    assert rows == []


@pytest.mark.asyncio
async def test_approve_with_final_name_rewrites(db, sample_skill):
    catalog = SkillCatalog()
    pending = await catalog.stage_for_review(
        agent_name="alpha", namespace="ns", skill=sample_skill
    )
    await catalog.decide(
        SkillReviewDecision(
            review_id=pending.review_id,
            decision="approve",
            reviewer="op",
            final_name="telegram-messenger",
            final_description="edited description",
        )
    )
    rows = await catalog.list_approved_for_agent("alpha")
    assert rows[0].name == "telegram-messenger"
    assert rows[0].description == "edited description"
