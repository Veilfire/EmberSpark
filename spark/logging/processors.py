"""structlog processors: SecretStr unwrap, secret-value scrubbing, redaction hook."""

from __future__ import annotations

import re
from typing import Any, Callable, MutableMapping

from pydantic import SecretStr

from spark.logging.events import EventType

_COMMON_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"AKIA[0-9A-Z]{16}"),                 # AWS access key id
    re.compile(r"sk-[A-Za-z0-9]{20,}"),              # OpenAI-ish
    re.compile(r"sk-or-[A-Za-z0-9-]{20,}"),          # OpenRouter
    re.compile(r"sk-ant-[A-Za-z0-9-]{20,}"),         # Anthropic
    re.compile(r"ghp_[A-Za-z0-9]{36,}"),             # GitHub PAT
    re.compile(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"),  # JWT
    re.compile(r"-----BEGIN (?:RSA |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),     # Slack
)

_PLACEHOLDER = "***"


def _scrub_string(value: str, extra_values: frozenset[str]) -> tuple[str, bool]:
    scrubbed = value
    changed = False
    for raw in extra_values:
        if raw and raw in scrubbed:
            scrubbed = scrubbed.replace(raw, _PLACEHOLDER)
            changed = True
    for pattern in _COMMON_SECRET_PATTERNS:
        new, n = pattern.subn(_PLACEHOLDER, scrubbed)
        if n:
            scrubbed = new
            changed = True
    return scrubbed, changed


def make_scrub_processor(
    tracked_values: Callable[[], frozenset[str]],
) -> Callable[[Any, str, MutableMapping[str, Any]], MutableMapping[str, Any]]:
    """Build a structlog processor that scrubs secrets from every string leaf."""

    def processor(
        _logger: Any, _method: str, event_dict: MutableMapping[str, Any]
    ) -> MutableMapping[str, Any]:
        extra = tracked_values()
        any_change = False

        def recurse(obj: Any) -> Any:
            nonlocal any_change
            if isinstance(obj, SecretStr):
                any_change = True
                return _PLACEHOLDER
            if isinstance(obj, str):
                new, changed = _scrub_string(obj, extra)
                if changed:
                    any_change = True
                return new
            if isinstance(obj, dict):
                return {k: recurse(v) for k, v in obj.items()}
            if isinstance(obj, list):
                return [recurse(v) for v in obj]
            if isinstance(obj, tuple):
                return tuple(recurse(v) for v in obj)
            return obj

        new_dict: MutableMapping[str, Any] = {}
        for k, v in event_dict.items():
            new_dict[k] = recurse(v)
        if any_change:
            new_dict.setdefault("redaction_applied", True)
        return new_dict

    return processor


def event_enum_processor(
    _logger: Any, _method: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """Reject free-string event types; coerce EventType to its value."""
    et = event_dict.get("event_type")
    if isinstance(et, EventType):
        event_dict["event_type"] = et.value
    elif isinstance(et, str):
        # enforce membership
        try:
            event_dict["event_type"] = EventType(et).value
        except ValueError as exc:
            raise ValueError(f"Unknown event_type {et!r}") from exc
    return event_dict
