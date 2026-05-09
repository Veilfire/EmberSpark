"""Tests that the logging processor scrubs secrets from every leaf."""

from __future__ import annotations

from pydantic import SecretStr

from spark.logging.processors import make_scrub_processor


def test_secret_str_unwrapped() -> None:
    proc = make_scrub_processor(lambda: frozenset())
    result = proc(None, "info", {"event": "x", "key": SecretStr("super-secret-value")})
    assert result["key"] == "***"
    assert result["redaction_applied"] is True


def test_tracked_value_replaced_everywhere() -> None:
    value = "top-secret-api-token-xyz"
    proc = make_scrub_processor(lambda: frozenset({value}))
    event = {
        "msg": f"authorization header: Bearer {value}",
        "nested": {"body": f"echo {value}"},
    }
    scrubbed = proc(None, "info", event)
    assert value not in str(scrubbed)


def test_regex_patterns_scrubbed() -> None:
    proc = make_scrub_processor(lambda: frozenset())
    event = {"response": "use sk-abcdefghijklmnopqrstuvwx1234567890"}
    scrubbed = proc(None, "info", event)
    assert "sk-abcdefghijklmnopqrstuvwx" not in scrubbed["response"]


def test_clean_event_untouched() -> None:
    proc = make_scrub_processor(lambda: frozenset())
    event = {"event": "tool.invoked", "plugin": "filesystem"}
    scrubbed = proc(None, "info", event.copy())
    assert "redaction_applied" not in scrubbed
    assert scrubbed["plugin"] == "filesystem"
