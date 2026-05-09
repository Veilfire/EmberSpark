"""Agent template registry (H1.1).

Discovers ready-to-run agent+task templates from ``examples/templates/``
and exposes them via a small in-memory registry. Used by the CLI
(``spark template list/show/install``) and the web UI (``/templates``).
"""

from __future__ import annotations

from spark.templates.loader import (
    Template,
    TemplateNotFound,
    TemplateValidationError,
    list_templates,
    load_template,
    templates_root,
)

__all__ = [
    "Template",
    "TemplateNotFound",
    "TemplateValidationError",
    "list_templates",
    "load_template",
    "templates_root",
]
