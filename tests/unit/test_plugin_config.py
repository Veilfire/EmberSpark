"""Unit tests for the plugin configuration system."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import BaseModel, Field

from spark.persistence.db import dispose, init_db
from spark.plugins.config import (
    empty_defaults,
    load_plugin_config,
    merge_config_and_args,
    reset_plugin_config,
    save_plugin_config,
    schema_hash,
)


class _ExampleSchema(BaseModel):
    allow_hosts: list[str] = Field(default_factory=list)
    allow_http: bool = False
    timeout_seconds: float = 5.0
    user_agent: str = "spark-runtime/0.1"
    internal_only_flag: bool = False  # operator-only; not in input_schema


@pytest.fixture
async def db(tmp_path: Path):
    await init_db(tmp_path / "spark.db")
    yield
    await dispose()


def test_schema_hash_deterministic() -> None:
    a = schema_hash(_ExampleSchema)
    b = schema_hash(_ExampleSchema)
    assert a == b and len(a) == 32


def test_empty_defaults_uses_field_defaults() -> None:
    defaults = empty_defaults(_ExampleSchema)
    assert defaults["allow_hosts"] == []
    assert defaults["allow_http"] is False
    assert defaults["timeout_seconds"] == 5.0


def test_merge_operator_overrides_model_on_overlapping_fields() -> None:
    config = {
        "allow_hosts": ["api.github.com"],
        "timeout_seconds": 3.0,
        "internal_only_flag": True,
    }
    # Model tries to widen the allowlist — operator must win.
    model_args = {"allow_hosts": ["evil.com"], "url": "https://api.github.com/foo"}
    input_fields = {"allow_hosts", "allow_http", "timeout_seconds", "url"}
    merged, operator_only = merge_config_and_args(
        config=config, args=model_args, input_field_names=input_fields
    )
    # allow_hosts: operator wins
    assert merged["allow_hosts"] == ["api.github.com"]
    # timeout: operator wins
    assert merged["timeout_seconds"] == 3.0
    # url: model-only field survives
    assert merged["url"] == "https://api.github.com/foo"
    # internal_only_flag is NOT in input_fields → goes to operator_only
    assert operator_only["internal_only_flag"] is True


@pytest.mark.asyncio
async def test_load_plugin_config_seeds_on_first_access(db) -> None:
    loaded = await load_plugin_config("example", _ExampleSchema)
    assert loaded.fresh is True
    assert loaded.defaults["timeout_seconds"] == 5.0

    loaded2 = await load_plugin_config("example", _ExampleSchema)
    assert loaded2.fresh is False


@pytest.mark.asyncio
async def test_save_and_reload(db) -> None:
    await save_plugin_config(
        plugin_name="example",
        config_schema=_ExampleSchema,
        raw={"allow_hosts": ["api.github.com"], "timeout_seconds": 7.5},
        updated_by="tester",
    )
    loaded = await load_plugin_config("example", _ExampleSchema)
    assert loaded.defaults["allow_hosts"] == ["api.github.com"]
    assert loaded.defaults["timeout_seconds"] == 7.5


@pytest.mark.asyncio
async def test_reset_drops_row(db) -> None:
    await save_plugin_config(
        plugin_name="example",
        config_schema=_ExampleSchema,
        raw={"allow_hosts": ["x"]},
        updated_by="tester",
    )
    removed = await reset_plugin_config("example", updated_by="tester")
    assert removed is True
    # Reload returns fresh defaults again.
    loaded = await load_plugin_config("example", _ExampleSchema)
    assert loaded.defaults["allow_hosts"] == []
