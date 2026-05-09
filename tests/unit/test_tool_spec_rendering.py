"""Tool-spec rendering tests — operator constraints in the system prompt.

The planner reads the rendered block to choose argument values. When the
operator has narrowed a plugin (allow_paths, allow_hosts, rules, etc.),
the model needs to see those values verbatim — otherwise it falls back
to common conventions ("I'll write to ~/output.md") and trips
PATH_DENIED on the first call.

These tests pin the rendering shape so a regression that drops the
constraint block will fail in CI.
"""

from __future__ import annotations

from typing import Any

import pytest

from spark.plugins.registry import default_registry
from spark.runtime.tool_spec import (
    _format_constraint_value,
    _is_constraint_field,
    render_tools_block,
)


@pytest.fixture(scope="module")
def registry() -> Any:
    return default_registry()


def test_render_without_configs_omits_constraint_block(registry: Any) -> None:
    block = render_tools_block(["markdown_writer"], registry)
    assert "### `markdown_writer`" in block
    assert "Operator config" not in block


def test_render_with_configs_emits_allow_paths(registry: Any) -> None:
    configs = {
        "markdown_writer": {
            "allow_paths": ["/data/spark-volume/deliverables"],
            "deny_paths": [],
            "allow_append": True,
            "allow_overwrite": True,
        }
    }
    block = render_tools_block(["markdown_writer"], registry, configs=configs)
    assert "Operator config (effective for this run):" in block
    assert "/data/spark-volume/deliverables" in block
    assert "allow_append: true" in block
    assert "allow_overwrite: true" in block


def test_render_with_empty_allow_paths_flags_unusable_state(registry: Any) -> None:
    """Empty allow_paths lists should be visually distinct so the model
    sees the problem rather than guessing at a path."""
    configs = {"markdown_writer": {"allow_paths": [], "deny_paths": []}}
    block = render_tools_block(["markdown_writer"], registry, configs=configs)
    assert "[] (empty — plugin will refuse calls until set)" in block


def test_render_with_http_tool_rules(registry: Any) -> None:
    configs = {
        "http_tool": {
            "rules": [
                {"host": "*", "allowed_methods": ["GET"], "extract_main_content": True},
                {"host": "api.github.com", "allowed_methods": ["GET", "POST"]},
            ],
            "default_max_response_bytes": 5_000_000,
        }
    }
    block = render_tools_block(["http_tool"], registry, configs=configs)
    assert "rules:" in block
    assert "api.github.com" in block or "host" in block


def test_render_skips_noise_fields(registry: Any) -> None:
    """Fields like ``user_agent`` / ``connect_timeout_seconds`` shouldn't
    surface as operator constraints — they don't gate behavior."""
    configs = {
        "web_search": {
            "provider": "ddg_html",
            "user_agent": "spark/0.1",
            "connect_timeout_seconds": 5.0,
            "max_results": 10,
        }
    }
    block = render_tools_block(["web_search"], registry, configs=configs)
    assert "provider:" in block
    assert "user_agent" not in block
    assert "connect_timeout_seconds" not in block


def test_is_constraint_field_heuristic() -> None:
    assert _is_constraint_field("allow_paths")
    assert _is_constraint_field("deny_paths")
    assert _is_constraint_field("allow_hosts")
    assert _is_constraint_field("allowed_methods")
    assert _is_constraint_field("rules")
    assert _is_constraint_field("enabled")
    assert _is_constraint_field("provider")
    assert _is_constraint_field("databases")
    assert _is_constraint_field("allow_chat_ids")
    assert _is_constraint_field("allow_to_domains")
    assert not _is_constraint_field("user_agent")
    assert not _is_constraint_field("connect_timeout_seconds")
    assert not _is_constraint_field("max_response_bytes")  # not a gate
    assert not _is_constraint_field("safe_search")


def test_format_constraint_value_clips_long_lists() -> None:
    short = _format_constraint_value(["a", "b"])
    assert "'a'" in short and "'b'" in short
    long = _format_constraint_value([str(i) for i in range(20)])
    assert "+12 more" in long


def test_format_constraint_value_handles_primitives() -> None:
    assert _format_constraint_value(True) == "true"
    assert _format_constraint_value(False) == "false"
    assert _format_constraint_value(None) == "null"
    assert _format_constraint_value([]) == "[] (empty — plugin will refuse calls until set)"
    assert _format_constraint_value("hello") == "'hello'"


def test_render_unknown_plugin_still_marked(registry: Any) -> None:
    block = render_tools_block(["nonexistent_plugin"], registry)
    assert "not registered" in block
