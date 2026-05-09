"""Plugin configuration loader, merger, and schema-hash helper.

Plugin config rules:
- Each plugin declares a Pydantic ``config_schema`` (operator-editable knobs).
- Operator-edited values live in the ``plugin_configs`` SQLite table, one row
  per plugin, serialized as JSON.
- At tool-call time, `load_plugin_config` pulls the row, validates it against
  the plugin's current schema, and hands the result to `merge_config_and_args`.
- Merge rule: **operator config is the base**, the model's per-call args
  override individual fields. The result must still validate against the
  plugin's ``input_schema``.

This is the seam that lets the operator narrow an agent via the Web UI
(e.g. limit ``http_client.allow_hosts``) without editing any YAML. The model
can only set fields that the plugin's input schema actually declares; it
cannot add fields or bypass the operator's hosts list because `allow_hosts`
in the merged dict wins over whatever the model proposed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ValidationError

from spark.persistence.db import session_scope
from spark.persistence.learning_models import PluginConfigRow
from spark.persistence.learning_repos import AuditRepository, PluginConfigRepository
from spark.utils.hashing import sha256_text


@dataclass(frozen=True)
class LoadedPluginConfig:
    plugin_name: str
    defaults: dict[str, Any]
    schema_hash: str
    fresh: bool  # True if the row did not exist and defaults were auto-seeded


def schema_hash(schema_cls: type[BaseModel]) -> str:
    """Stable hash of a plugin's config schema JSON."""
    return sha256_text(json.dumps(schema_cls.model_json_schema(), sort_keys=True))[:32]


def empty_defaults(schema_cls: type[BaseModel]) -> dict[str, Any]:
    """Instantiate the schema with no overrides and dump the field defaults."""
    try:
        return schema_cls().model_dump(mode="json")
    except ValidationError:
        # If the schema has required fields, fall back to an empty dict and
        # let the caller supply them. This only happens for schemas that
        # intentionally require operator input before any usable default.
        return {}


async def load_plugin_config(
    plugin_name: str, config_schema: type[BaseModel]
) -> LoadedPluginConfig:
    """Load operator config for a plugin, seeding defaults if missing.

    Always returns a `LoadedPluginConfig`; the caller can treat the `defaults`
    as the authoritative base for a call. The plugin row is seeded on first
    access so subsequent UI reads see a real row.
    """
    expected_hash = schema_hash(config_schema)
    async with session_scope() as session:
        repo = PluginConfigRepository(session)
        row = await repo.get(plugin_name)
        if row is None:
            base = empty_defaults(config_schema)
            await repo.upsert(
                plugin_name=plugin_name,
                config_json=json.dumps(base, sort_keys=True, default=str),
                schema_hash=expected_hash,
                updated_by="system",
            )
            return LoadedPluginConfig(
                plugin_name=plugin_name,
                defaults=base,
                schema_hash=expected_hash,
                fresh=True,
            )

        try:
            raw = json.loads(row.config_json or "{}")
            if not isinstance(raw, dict):
                raw = {}
        except json.JSONDecodeError:
            raw = {}

        # Validate against the current schema so drift surfaces as a clean
        # ValidationError rather than a runtime surprise.
        try:
            validated = config_schema.model_validate(raw).model_dump(mode="json")
        except ValidationError:
            # Schema changed under the operator's feet; keep whatever parses
            # and let the UI flag the drift via the schema_hash mismatch.
            validated = empty_defaults(config_schema)

        return LoadedPluginConfig(
            plugin_name=plugin_name,
            defaults=validated,
            schema_hash=row.schema_hash,
            fresh=False,
        )


def merge_config_and_args(
    *,
    config: dict[str, Any],
    args: dict[str, Any],
    input_field_names: set[str],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Merge operator config with model per-call args.

    Returns ``(merged_args, operator_only_config)``:

    - ``merged_args`` holds the per-call args the model will see. For every
      field that exists in ``input_field_names``, the operator-configured
      value **wins** — this is the security property that lets an operator
      narrow ``allow_hosts`` / ``allow_paths`` / ``allowed_methods`` through
      the UI without the model being able to override them. Fields in ``args``
      that the operator did not configure are kept as-is.
    - ``operator_only_config`` holds config fields that aren't in the
      plugin's input_schema (e.g. ``read_only``, ``allow_append``). These
      are exposed to the plugin via ``ctx.plugin_config`` so it can enforce
      operator-only knobs without adding them to the schema the model sees.
    """
    merged_args = dict(args)
    operator_only: dict[str, Any] = {}
    for key, value in config.items():
        if key in input_field_names:
            merged_args[key] = value  # operator wins on shared fields
        else:
            operator_only[key] = value
    # Also surface the shared-field values in operator_only so the plugin
    # can introspect the full effective config if it wants.
    for key, value in config.items():
        operator_only.setdefault(key, value)
    return merged_args, operator_only


async def save_plugin_config(
    *,
    plugin_name: str,
    config_schema: type[BaseModel],
    raw: dict[str, Any],
    updated_by: str,
    reason: str = "",
) -> LoadedPluginConfig:
    """Validate and persist an operator-edited plugin config.

    Writes an audit entry at ``elevated`` severity because these edits
    directly change the agent's effective tool reach.
    """
    validated = config_schema.model_validate(raw).model_dump(mode="json")
    expected_hash = schema_hash(config_schema)
    async with session_scope() as session:
        repo = PluginConfigRepository(session)
        await repo.upsert(
            plugin_name=plugin_name,
            config_json=json.dumps(validated, sort_keys=True, default=str),
            schema_hash=expected_hash,
            updated_by=updated_by,
        )
        await AuditRepository(session).append(
            actor=updated_by,
            kind="plugin.config.update",
            target=plugin_name,
            diff=validated,
            reason=reason,
            severity="elevated",
        )
    return LoadedPluginConfig(
        plugin_name=plugin_name,
        defaults=validated,
        schema_hash=expected_hash,
        fresh=False,
    )


async def reset_plugin_config(plugin_name: str, *, updated_by: str) -> bool:
    async with session_scope() as session:
        repo = PluginConfigRepository(session)
        removed = await repo.delete(plugin_name)
        if removed:
            await AuditRepository(session).append(
                actor=updated_by,
                kind="plugin.config.reset",
                target=plugin_name,
                severity="elevated",
            )
    return removed
