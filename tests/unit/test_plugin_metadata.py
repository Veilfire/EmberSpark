"""Plugin metadata contract — enforced as a build gate.

Every registered tool plugin must expose enough metadata for the
planner to use it without guessing. Specifically:

1. Class-level ``description`` is non-empty.
2. Every field in ``input_schema`` has a non-empty ``description``.

The runtime renders both into the planner's system prompt and into the
native ``bind_tools`` payload. Without descriptions the planner sees
arg names alone and has to infer semantics — fragile across providers
and prompt-length-regression-prone. This test fails fast if a new
plugin lands without docs.

If you're adding a plugin and this test fails, see
[Plugin Authoring](../../wiki/Plugin-Authoring.md) for the contract
and the example plugin.
"""

from __future__ import annotations

from typing import Any

import pytest

from spark.plugins.registry import default_registry


@pytest.fixture(scope="module")
def registry() -> Any:
    return default_registry()


def test_every_plugin_has_class_description(registry: Any) -> None:
    """Class-level ``description`` is required + must be non-trivial."""
    bad: list[str] = []
    for name in registry.names():
        cls = registry.get(name).cls
        desc = (getattr(cls, "description", None) or "").strip()
        if not desc:
            bad.append(f"{name}: empty description")
        elif len(desc) < 10:
            bad.append(f"{name}: description too short ({desc!r})")
    assert not bad, "Plugin description contract:\n  " + "\n  ".join(bad)


def test_every_input_schema_field_has_description(registry: Any) -> None:
    """Every Pydantic Field in ``input_schema`` must set ``description=``.

    This is the **load-bearing** check — the planner uses these
    strings to choose argument values. Without them the model can
    only guess from field names.
    """
    bad: list[str] = []
    for name in registry.names():
        cls = registry.get(name).cls
        schema = cls.input_schema.model_json_schema()
        props = schema.get("properties", {})
        for fname, prop in props.items():
            if not isinstance(prop, dict):
                continue
            descr = (prop.get("description") or "").strip()
            if not descr:
                bad.append(f"{name}.{fname}")

    assert not bad, (
        f"{len(bad)} plugin input fields missing ``description=``. "
        "Add a Pydantic Field(..., description='...') for each. "
        "See wiki/Plugin-Authoring.md.\n  " + "\n  ".join(bad)
    )


def test_native_tool_specs_round_trip(registry: Any) -> None:
    """``build_native_tool_specs`` produces an OpenAI-shaped dict per
    allowlisted plugin so ``bind_tools`` never gets garbage."""
    from spark.runtime.tool_spec import build_native_tool_specs

    specs = build_native_tool_specs(list(registry.names()), registry)
    assert len(specs) == len(registry.names())
    for s in specs:
        assert s["type"] == "function"
        fn = s["function"]
        assert isinstance(fn["name"], str) and fn["name"]
        assert isinstance(fn["description"], str) and fn["description"]
        params = fn["parameters"]
        assert isinstance(params, dict)
        assert params.get("type") == "object"
        assert "properties" in params


def test_render_tools_block_is_non_empty(registry: Any) -> None:
    """``render_tools_block`` must surface the description + every arg."""
    from spark.runtime.tool_spec import render_tools_block

    rendered = render_tools_block(list(registry.names()), registry)
    for name in registry.names():
        assert f"`{name}`" in rendered, f"plugin {name} missing from rendered block"
        cls = registry.get(name).cls
        # Every argument name appears in the rendered block.
        for fname in cls.input_schema.model_json_schema().get("properties", {}):
            assert f"**{fname}**" in rendered, (
                f"{name}.{fname} not in rendered tools block"
            )
