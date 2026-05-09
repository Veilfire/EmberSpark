"""Tool-spec rendering — turn plugin schemas into prompt-ready text + native
``bind_tools`` payloads.

Two consumers, one source of truth:

1. ``render_tools_block(plugins)`` produces a compact markdown spec for the
   system prompt. Every model — even ones that don't support native
   tool-binding (Ollama, older Bedrock variants) — sees the description,
   the args list, types, defaults, enums, and constraints. This is what
   removes the "agent guesses tool semantics from name alone" problem.

2. ``build_native_tool_specs(plugins)`` produces the OpenAI-style
   ``{"type": "function", "function": {...}}`` dicts that
   ``ChatModel.bind_tools(...)`` expects. Providers convert these to
   their native tool-calling format (Anthropic's ``tools`` array,
   OpenAI's ``tools`` array, etc.) so the model emits structured
   ``tool_calls`` instead of text JSON.

Both readers consume the same ``input_schema.model_json_schema()`` so
plugin authors only have to maintain one source of truth — Pydantic
``Field(description=…)``.
"""

from __future__ import annotations

from typing import Any, Iterable

from spark.plugins.registry import PluginHandle, PluginRegistry


def render_tools_block(
    plugin_names: Iterable[str],
    registry: PluginRegistry,
    configs: dict[str, dict[str, Any]] | None = None,
) -> str:
    """Markdown table of every requested plugin's API.

    Iterates only ``plugin_names`` (typically the agent's
    ``spec.plugins.allow``). Skips unregistered names with a placeholder
    so the operator notices the typo in the prompt.

    When ``configs`` is supplied — typically the operator-stored config
    loaded via ``spark.plugins.config.load_plugin_config`` for each
    plugin — the rendering also includes an "Operator config" block per
    plugin listing the values that bound the model's reach (allow_paths,
    allow_hosts, rules, allowed_methods, enabled toggles, etc.). The
    model can then propose argument values inside the actual constraints
    instead of guessing common conventions and tripping ``PATH_DENIED``
    or ``URL_DENIED`` on the first call.
    """
    chunks: list[str] = ["## Available tools"]
    for name in plugin_names:
        try:
            handle = registry.get(name)
        except KeyError:
            chunks.append(f"\n### {name}\n_(not registered — check the agent's plugins.allow)_")
            continue
        chunks.append(_render_one(name, handle, (configs or {}).get(name)))
    return "\n".join(chunks)


def _render_one(
    name: str,
    handle: PluginHandle,
    config: dict[str, Any] | None = None,
) -> str:
    cls = handle.cls
    desc = (cls.description or "").strip() or "_(no description)_"
    schema = cls.input_schema.model_json_schema()
    out = [f"\n### `{name}`", desc, ""]

    props = schema.get("properties", {})
    required = set(schema.get("required", []))
    if props:
        out.append("Args:")
        for fname, prop in props.items():
            if not isinstance(prop, dict):
                continue
            out.append(_render_arg(fname, prop, fname in required))
    else:
        out.append("_(no arguments)_")

    constraints = _render_operator_constraints(handle, config)
    if constraints:
        out.extend(constraints)
    return "\n".join(out)


# Heuristic name patterns for config fields worth surfacing to the model.
# We keep this intentionally narrow — the goal is to show fields that
# *gate behavior* (paths, hosts, methods, toggles) without flooding the
# prompt with timeouts, user-agent strings, and other noise.
_CONSTRAINT_NAME_PATTERNS = (
    "allow",
    "deny",
    "enabled",
    "host",
    "path",
    "rule",
    "provider",
    "database",
    "repo",
    "chat_id",
    "model",  # `allow_models`, `model_kwargs`, etc.
    "domain",
)


def _is_constraint_field(field_name: str) -> bool:
    n = field_name.lower()
    return any(pat in n for pat in _CONSTRAINT_NAME_PATTERNS)


def _render_operator_constraints(
    handle: PluginHandle, config: dict[str, Any] | None
) -> list[str]:
    """Return the "Operator config" lines for a plugin, or [].

    Surfaces:

    - Every field whose name overlaps with the plugin's ``input_schema``
      (those operator values are *enforced* — the merge layer rewrites
      the model's per-call value with the operator's, so the model needs
      to know them ahead of time).
    - Plus any other field whose name matches a curated list of patterns
      that imply gating behavior (``allow_*``, ``deny_*``, ``enabled``,
      ``*_host*``, ``*_path*``, ``rules``, etc.).

    Empty configs and configs with only noise (timeouts, user agents)
    return an empty list so the prompt stays compact.
    """
    if not config:
        return []
    cls = handle.cls
    try:
        input_fields = set(
            cls.input_schema.model_json_schema().get("properties", {}).keys()
        )
    except Exception:
        input_fields = set()

    interesting: list[tuple[str, Any]] = []
    for field, value in config.items():
        if field in input_fields or _is_constraint_field(field):
            interesting.append((field, value))
    if not interesting:
        return []

    out = ["", "Operator config (effective for this run):"]
    for fname, value in interesting:
        out.append(f"  - {fname}: {_format_constraint_value(value)}")
    return out


def _format_constraint_value(value: Any) -> str:
    """Render a constraint value compactly without tripping prompt limits.

    Lists are clipped to 8 elements; dicts that exceed ~200 chars are
    summarized; long strings are truncated with an ellipsis. The exact
    value lives in the operator config and the audit log, so we're
    optimizing for "model sees the shape" rather than a bit-perfect dump.
    """
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list):
        if not value:
            return "[] (empty — plugin will refuse calls until set)"
        clipped = value[:8]
        rendered = ", ".join(_format_constraint_value(v) for v in clipped)
        suffix = f" (+{len(value) - 8} more)" if len(value) > 8 else ""
        return f"[{rendered}]{suffix}"
    if isinstance(value, dict):
        rendered = repr(value)
        if len(rendered) > 200:
            return f"{{ {len(value)} keys: {', '.join(list(value)[:6])}{' …' if len(value) > 6 else ''} }}"
        return rendered
    if isinstance(value, str):
        if len(value) > 200:
            return repr(value[:200] + "…")
        return repr(value)
    return repr(value)


def _render_arg(name: str, prop: dict[str, Any], required: bool) -> str:
    parts: list[str] = []

    # Type — collapse anyOf with null into "T (optional)".
    t = _type_label(prop)
    parts.append(t)

    if required:
        parts.append("required")
    elif "default" in prop:
        parts.append(f"default {_format_default(prop['default'])}")

    # Enum values.
    enum = prop.get("enum")
    if isinstance(enum, list):
        rendered = " | ".join(repr(v) for v in enum)
        parts.append(f"one of: {rendered}")

    # String length range / pattern.
    if "minLength" in prop and "maxLength" in prop:
        parts.append(f"{prop['minLength']}-{prop['maxLength']} chars")
    elif "maxLength" in prop:
        parts.append(f"max {prop['maxLength']} chars")

    # Numeric range — for completeness; we deliberately strip these from
    # most plugin schemas because Bedrock's tool-calling subset rejects
    # them, but a plugin might still set them for non-Bedrock-bound
    # constraints. Render anyway when present.
    lo, hi = prop.get("minimum"), prop.get("maximum")
    if lo is not None and hi is not None:
        parts.append(f"range {lo}..{hi}")

    descr = prop.get("description") or ""
    head = f"  - **{name}** ({', '.join(parts)})"
    if descr:
        return f"{head}: {descr}"
    return head


def _type_label(prop: dict[str, Any]) -> str:
    """Human-readable type label that flattens ``anyOf [..., null]``."""
    if "anyOf" in prop and isinstance(prop["anyOf"], list):
        non_null = [v for v in prop["anyOf"] if isinstance(v, dict) and v.get("type") != "null"]
        if len(non_null) == 1:
            return _type_label(non_null[0]) + ", optional"
        return "any-of: " + " | ".join(_type_label(v) for v in non_null)

    t = prop.get("type")
    if t == "array":
        items = prop.get("items") or {}
        if isinstance(items, dict):
            inner = _type_label(items)
            return f"list[{inner}]"
        return "list"
    if t == "object":
        return "object"
    if t in ("string", "integer", "number", "boolean"):
        return t
    if "$ref" in prop:
        return prop["$ref"].rsplit("/", 1)[-1]
    return "any"


def _format_default(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, str):
        return repr(value)
    if isinstance(value, (list, dict)):
        return "[]" if value == [] else "{}" if value == {} else repr(value)
    return repr(value)


# ---------------------------------------------------------------------------
# Native bind_tools payloads
# ---------------------------------------------------------------------------


def build_native_tool_specs(
    plugin_names: Iterable[str], registry: PluginRegistry
) -> list[dict[str, Any]]:
    """OpenAI-style tool dicts suitable for ``ChatModel.bind_tools``.

    LangChain's provider adapters convert this canonical shape to each
    backend's native tool-calling format (Anthropic's ``tools``,
    OpenAI's ``tools``, Bedrock-via-OpenRouter's mapped equivalent).
    """
    specs: list[dict[str, Any]] = []
    for name in plugin_names:
        try:
            handle = registry.get(name)
        except KeyError:
            continue
        cls = handle.cls
        params = cls.input_schema.model_json_schema()
        # Strip ``$defs`` and ``title`` — they're cosmetic and some
        # providers complain about extra root-level keys. The function
        # parameters object itself stays JSON-Schema-valid.
        params = {k: v for k, v in params.items() if k != "title"}
        specs.append(
            {
                "type": "function",
                "function": {
                    "name": cls.name,
                    "description": (cls.description or "").strip(),
                    "parameters": params,
                },
            }
        )
    return specs
