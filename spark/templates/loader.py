"""Template discovery + validation.

Each template is a directory under ``examples/templates/`` containing:

- ``README.md`` — operator-facing walkthrough
- ``agent.yaml`` — the agent spec
- ``task.yaml`` — the task spec
- ``plugin-config.hints.json`` — non-applied reference configs for the
  plugins this template uses (informational only — the operator
  populates the real configs via the Plugins UI)

At import time we **validate** each template by running its YAMLs
through the existing :func:`spark.config.loader.load_agent` /
:func:`spark.config.loader.load_task` — any template that fails to
parse raises a clear error at startup so broken templates never ship
past CI.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# A module-level cache so repeated `list_templates` / `load_template`
# calls don't re-read the filesystem. Invalidated on process restart.
_CACHE: dict[str, "Template"] | None = None


class TemplateNotFound(KeyError):
    """Raised when a template name isn't in the registry."""


class TemplateValidationError(RuntimeError):
    """Raised when a template's agent.yaml or task.yaml won't parse."""


@dataclass(frozen=True)
class Template:
    """A fully-resolved template discovered on disk."""

    name: str
    directory: Path
    readme: str
    agent_yaml: str             # raw bytes as UTF-8 — the file contents, unparsed
    task_yaml: str              # same
    plugin_config_hints: dict[str, Any]
    # Derived / parsed metadata
    description: str            # one-line from the agent's `spec.description`
    plugins_required: list[str]     # from `spec.plugins.allow`
    permissions_required: list[str] # from `spec.permissions.grants`
    secrets_required: list[str] = field(default_factory=list)

    def to_summary(self) -> dict[str, Any]:
        """Serialize for the API listing (no full YAMLs)."""
        return {
            "name": self.name,
            "description": self.description,
            "plugins_required": self.plugins_required,
            "permissions_required": self.permissions_required,
            "secrets_required": self.secrets_required,
        }


def templates_root() -> Path:
    """Return the absolute path to ``examples/templates/``.

    Checks two candidate locations:

    1. Source checkout: ``<repo>/examples/templates/`` — walk up three
       levels from this file (``spark/templates/loader.py``).
    2. Installed package: ``<site-packages>/examples/templates/`` — walk
       up two levels from the ``spark`` package directory. The Docker
       image copies templates here so they ship with the wheel.

    Returns the first candidate that exists on disk, or the source path
    as a fallback (which ``list_templates`` treats as empty).
    """
    pkg = Path(__file__).resolve().parent.parent       # .../spark/
    # Source checkout: pkg is .../spark/, pkg.parent is repo root
    # → <repo>/examples/templates/
    source = pkg.parent / "examples" / "templates"
    if source.is_dir():
        return source
    # Installed package: pkg is .../site-packages/spark/,
    # pkg.parent is .../site-packages/
    # Docker COPY puts templates at .../site-packages/examples/templates/
    installed = pkg.parent / "examples" / "templates"
    if installed.is_dir():
        return installed
    return source  # fallback — list_templates() returns [] if missing


def list_templates() -> list[Template]:
    """Discover + validate every template. Result is cached per-process."""
    global _CACHE
    if _CACHE is not None:
        return sorted(_CACHE.values(), key=lambda t: t.name)

    root = templates_root()
    if not root.exists() or not root.is_dir():
        _CACHE = {}
        return []

    cache: dict[str, Template] = {}
    for entry in sorted(root.iterdir()):
        if not entry.is_dir():
            continue
        if entry.name.startswith("."):
            continue
        try:
            tpl = _load_one(entry)
        except (TemplateValidationError, Exception) as exc:
            # Log but skip — don't let one broken template block the
            # rest from loading. The API surfaces the error in the
            # response detail.
            import warnings

            warnings.warn(
                f"template {entry.name!r} skipped: {type(exc).__name__}: {exc}",
                stacklevel=1,
            )
            continue
        cache[tpl.name] = tpl

    _CACHE = cache
    return sorted(cache.values(), key=lambda t: t.name)


def load_template(name: str) -> Template:
    """Look up a template by name (triggers discovery if not cached)."""
    templates = {t.name: t for t in list_templates()}
    if name not in templates:
        raise TemplateNotFound(name)
    return templates[name]


def _invalidate_cache() -> None:
    """Test helper — force a re-scan on next call."""
    global _CACHE
    _CACHE = None


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _load_one(directory: Path) -> Template:
    name = directory.name

    readme_path = directory / "README.md"
    agent_path = directory / "agent.yaml"
    task_path = directory / "task.yaml"
    hints_path = directory / "plugin-config.hints.json"

    for required in (readme_path, agent_path, task_path):
        if not required.exists():
            raise TemplateValidationError(
                f"template {name!r} is missing required file {required.name!r}"
            )

    readme = readme_path.read_text(encoding="utf-8")
    agent_yaml_text = agent_path.read_text(encoding="utf-8")
    task_yaml_text = task_path.read_text(encoding="utf-8")

    hints: dict[str, Any] = {}
    if hints_path.exists():
        try:
            hints = json.loads(hints_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise TemplateValidationError(
                f"template {name!r}: plugin-config.hints.json is not valid JSON: {exc}"
            ) from exc

    # Parse + validate the YAMLs via the existing config loader so broken
    # templates fail CI and never reach operators. Lazy import to avoid a
    # circular: spark.config.loader doesn't depend on spark.templates.
    from spark.config.loader import ConfigLoadError, load_agent, load_task

    try:
        agent = load_agent(agent_path)
    except ConfigLoadError as exc:
        raise TemplateValidationError(
            f"template {name!r}: agent.yaml failed to parse: {exc.errors}"
        ) from exc
    try:
        task = load_task(task_path)
    except ConfigLoadError as exc:
        raise TemplateValidationError(
            f"template {name!r}: task.yaml failed to parse: {exc.errors}"
        ) from exc

    # Sanity: the task's `spec.agent` should match the agent's metadata.name
    # so `spark template install` produces a consistent pair.
    if task.spec.agent != agent.metadata.name:
        raise TemplateValidationError(
            f"template {name!r}: task.spec.agent={task.spec.agent!r} does not "
            f"match agent.metadata.name={agent.metadata.name!r}"
        )

    # Derive metadata
    description = (agent.spec.description or "").strip().split("\n", 1)[0]
    plugins_required = sorted(agent.spec.plugins.allow)
    permissions_required = sorted(g.value for g in agent.spec.permissions.grants)

    # Try to derive secrets from the agent spec (api_key_ref) and hints.
    secrets: set[str] = set()
    provider = getattr(agent.spec.runtime.provider, "api_key_ref", None)
    if provider:
        secrets.add(provider)
    for plugin_name, hint in hints.items():
        if not isinstance(hint, dict):
            continue
        for key, value in hint.items():
            if key.endswith("_secret") and isinstance(value, str):
                secrets.add(value)
            if key == "username_secret" or key == "password_secret":
                if isinstance(value, str):
                    secrets.add(value)

    return Template(
        name=name,
        directory=directory,
        readme=readme,
        agent_yaml=agent_yaml_text,
        task_yaml=task_yaml_text,
        plugin_config_hints=hints,
        description=description,
        plugins_required=plugins_required,
        permissions_required=permissions_required,
        secrets_required=sorted(secrets),
    )
