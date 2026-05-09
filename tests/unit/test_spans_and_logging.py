"""Tests for span tracing + log hash-chain + retention bucketing."""

from __future__ import annotations

import gzip
import json
from datetime import timedelta
from pathlib import Path

import pytest

from spark.logging.retention import (
    BUCKETS,
    bucket_for_age,
    latest_hash,
    rotate_and_bucket,
    verify_chain,
)
from spark.persistence.db import dispose, init_db, session_scope
from spark.persistence.learning_models import RunSpanRow
from spark.persistence.learning_repos import RunSpanRepository
from spark.runtime.spans import reset_run_id, set_run_id, span


@pytest.fixture
async def db(tmp_path: Path):
    await init_db(tmp_path / "spark.db")
    yield
    await dispose()


@pytest.mark.asyncio
async def test_span_persists_row_and_duration(db) -> None:
    token = set_run_id("run-span-test")
    try:
        async with span("outer", attr_1="x"):
            async with span("inner"):
                pass
    finally:
        reset_run_id(token)

    async with session_scope() as session:
        rows = await RunSpanRepository(session).list_for_run("run-span-test")
    assert len(rows) == 2
    names = [r.name for r in rows]
    assert "outer" in names and "inner" in names
    outer = next(r for r in rows if r.name == "outer")
    inner = next(r for r in rows if r.name == "inner")
    assert inner.parent_span_id == outer.id
    assert outer.duration_ms is not None and outer.duration_ms >= 0


@pytest.mark.asyncio
async def test_span_records_error_class(db) -> None:
    token = set_run_id("run-err-test")
    try:
        with pytest.raises(ValueError):
            async with span("failing"):
                raise ValueError("boom")
    finally:
        reset_run_id(token)

    async with session_scope() as session:
        rows = await RunSpanRepository(session).list_for_run("run-err-test")
    assert len(rows) == 1
    assert rows[0].error_class == "ValueError"


# --------------------------------------------------------------------------
# Retention + hash chain
# --------------------------------------------------------------------------


def test_bucket_for_age_boundaries() -> None:
    assert bucket_for_age(timedelta(hours=1)).name == "hot"
    assert bucket_for_age(timedelta(days=10)).name == "warm"
    assert bucket_for_age(timedelta(days=60)).name == "cold"
    assert bucket_for_age(timedelta(days=500)).name == "archive"


def test_verify_chain_empty_ok(tmp_path: Path) -> None:
    verdict = verify_chain(tmp_path)
    assert verdict.ok is True


def test_verify_chain_detects_break(tmp_path: Path) -> None:
    # Craft two fake rotated files with a broken hash chain.
    hot = tmp_path / "hot"
    hot.mkdir()
    first = hot / "a.jsonl"
    first.write_text(
        json.dumps({"event_type": "file.header", "prev_sha256": ""}) + "\n"
        + json.dumps({"event_type": "task.started"}) + "\n"
    )
    second = hot / "b.jsonl"
    second.write_text(
        json.dumps(
            {
                "event_type": "file.header",
                "prev_sha256": "totally-wrong-hash",
            }
        )
        + "\n"
    )
    verdict = verify_chain(tmp_path)
    assert verdict.ok is False
    assert verdict.broken_file is not None
    assert "prev_sha256" in verdict.message
