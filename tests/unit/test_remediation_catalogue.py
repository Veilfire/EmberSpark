"""Failure-mode surface — catalogue + serialization + dedup tests.

Three concerns:

1. Every :class:`ErrorCode` produces at least one :class:`TuningOption`
   when fed through ``options_for(...)``. New codes can't ship without
   a catalogue entry — keeps the inspector from rendering empty rows.
2. ``SparkError.to_dict()`` carries the new ``tuning`` field with the
   right shape (label / description / risk / severity / deep_link /
   prefill / audit_kind).
3. The gate-notification dedup helper is windowed by ``(agent, code,
   target)`` so a tight loop hits the bell once, not N times.
"""

from __future__ import annotations

import json
from base64 import urlsafe_b64decode

import pytest

from spark.errors.codes import ErrorCode, SparkError
from spark.errors.remediation import (
    TuningOption,
    options_for,
)


# ---------------------------------------------------------------------------
# 1. Catalogue coverage — every code returns at least one option
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("code", list(ErrorCode))
def test_catalogue_covers_every_code(code: ErrorCode) -> None:
    """Adding a new ErrorCode without a catalogue entry should fail loud."""
    opts = options_for(code, {})
    assert opts, f"{code.value}: catalogue produced no options"
    assert all(isinstance(o, TuningOption) for o in opts)
    # Every option has the four mandatory copy fields.
    for o in opts:
        assert o.label
        assert o.description
        assert o.risk
        assert o.severity in ("low", "medium", "high", "critical")


@pytest.mark.parametrize("code", list(ErrorCode))
def test_catalogue_first_option_is_safest(code: ErrorCode) -> None:
    """Order convention: safest (low) first, most aggressive last.

    Tests with realistic detail (not just ``{}``) so conditional
    branches are exercised — many catalogue entries only emit their
    mutating option when ``agent``/``path``/``host`` are present.
    """
    severities = ["low", "medium", "high", "critical"]
    realistic_detail = {
        "agent": "alice",
        "path": "/tmp/scratch/foo",
        "host": "example.com",
        "method": "POST",
        "plugin": "shell",
        "missing": ["fs.write"],
        "used": 20,
        "limit": 20,
        "classes": ["financial.card"],
        "scope": "tool_output",
        "secret_name": "STRIPE_KEY",
        "field": "rate_limit",
        "size": 1_000_000,
        "max_bytes": 500_000,
        "_message": (
            "Path /tmp/scratch/foo is outside allow list; "
            "Host 'example.com' is not in the allowlist"
        ),
    }
    opts = options_for(code, realistic_detail)
    if len(opts) <= 1:
        return
    indices = [severities.index(o.severity) for o in opts]
    assert indices == sorted(indices), (
        f"{code.value}: options out of safest-first order: "
        f"{[(o.severity, o.label) for o in opts]}"
    )


# ---------------------------------------------------------------------------
# 2. Concrete catalogue branches — pick representative codes
# ---------------------------------------------------------------------------


def test_path_denied_with_agent_pre_fills_parent_directory() -> None:
    err = SparkError(
        ErrorCode.PATH_DENIED,
        "Path /etc/passwd is outside allow list",
        detail={"path": "/etc/passwd", "agent": "researcher"},
    )
    opts = options_for(err.code, {**err.detail, "_message": err.message})
    add_option = next(o for o in opts if o.deep_link)
    assert add_option.prefill == {
        "kind": "fs_allow_path",
        "agent": "researcher",
        "path": "/etc",
    }
    assert add_option.deep_link.startswith("/security?")
    # /etc is on the heuristic-sensitive list — should bump severity.
    assert add_option.severity in ("high", "critical")


def test_path_denied_extracts_path_from_message_when_detail_bare() -> None:
    """Legacy raise sites pass the path in the message, not detail."""
    err = SparkError(
        ErrorCode.PATH_DENIED,
        "Path /tmp/scratch/foo is outside allow list",
        detail={"agent": "alice"},
    )
    opts = options_for(err.code, {**err.detail, "_message": err.message})
    actionable = [o for o in opts if o.deep_link]
    assert actionable, "expected at least one deep-linkable option"
    # Path got pulled out; suggested allow-list root is the parent.
    assert actionable[0].prefill["path"] == "/tmp/scratch"


def test_url_metadata_blocked_advertises_no_tuning() -> None:
    err = SparkError(
        ErrorCode.URL_METADATA_BLOCKED,
        "IP 169.254.169.254 is on the exact-match block list",
        detail={"url": "http://169.254.169.254/", "ip": "169.254.169.254"},
    )
    opts = options_for(err.code, err.detail)
    assert len(opts) == 1
    assert opts[0].deep_link is None
    assert opts[0].severity == "critical"


def test_data_class_blocked_offers_grant_with_target_class_prefilled() -> None:
    err = SparkError(
        ErrorCode.DATA_CLASS_BLOCKED,
        "Data class financial.card is blocked at scope tool_output",
        detail={
            "classes": ["financial.card"],
            "scope": "tool_output",
            "agent": "cc-processor",
            "matched_rule_ids": ["luhn"],
        },
    )
    opts = options_for(err.code, err.detail)
    grant = next(o for o in opts if o.audit_kind == "security.data_class.grant")
    assert grant.prefill["data_class"] == "financial.card"
    assert grant.prefill["agent"] == "cc-processor"
    assert grant.prefill["scope"] == "tool_output"
    assert grant.severity == "critical"


def test_budget_tool_exceeded_suggests_50pct_headroom() -> None:
    err = SparkError(
        ErrorCode.BUDGET_TOOL_EXCEEDED,
        "Tool budget exceeded",
        detail={"used": 20, "limit": 20, "agent": "alice"},
    )
    opts = options_for(err.code, err.detail)
    raise_opt = next(o for o in opts if o.deep_link)
    # 20 * 1.5 = 30
    assert raise_opt.prefill["suggested"] == 30
    assert raise_opt.prefill["field"] == "max_tool_calls"


# ---------------------------------------------------------------------------
# 3. SparkError.to_dict() carries tuning
# ---------------------------------------------------------------------------


def test_to_dict_includes_tuning() -> None:
    err = SparkError(
        ErrorCode.PATH_DENIED,
        "Path /etc/passwd is outside allow list",
        detail={"path": "/etc/passwd", "agent": "researcher"},
    )
    payload = err.to_dict()
    assert "tuning" in payload
    assert payload["tuning"], "tuning list should be non-empty"
    first = payload["tuning"][0]
    assert set(first.keys()) >= {
        "label",
        "description",
        "risk",
        "severity",
        "deep_link",
        "prefill",
        "audit_kind",
    }


def test_to_dict_does_not_leak_internal_message_marker() -> None:
    """We pass `_message` into the catalogue for legacy extraction —
    it must NOT appear back in the serialized detail (only the
    operator-facing fields)."""
    err = SparkError(
        ErrorCode.PATH_DENIED,
        "Path /etc/passwd is outside allow list",
        detail={"path": "/etc/passwd", "agent": "researcher"},
    )
    payload = err.to_dict()
    assert "_message" not in payload["detail"]


def test_deep_link_prefill_is_decodable_base64_json() -> None:
    err = SparkError(
        ErrorCode.PATH_DENIED,
        "Path /etc/passwd is outside allow list",
        detail={"path": "/etc/passwd", "agent": "researcher"},
    )
    payload = err.to_dict()
    actionable = [t for t in payload["tuning"] if t["deep_link"]]
    assert actionable
    link = actionable[0]["deep_link"]
    # Round-trip decode — what the frontend will do.
    encoded = link.split("prefill=", 1)[1]
    pad = "=" * (-len(encoded) % 4)
    decoded = json.loads(urlsafe_b64decode(encoded + pad))
    assert decoded == actionable[0]["prefill"]


# ---------------------------------------------------------------------------
# 4. Gate-notification dedup
# ---------------------------------------------------------------------------


def test_should_notify_dedup_window_per_agent_code_target() -> None:
    """Same key fires once; different keys fire independently."""
    from spark.errors.notify import _last_notified, _should_notify

    _last_notified.clear()
    key1 = ("alice", "SPK_E_PATH_DENIED", "/etc/passwd")
    key2 = ("alice", "SPK_E_PATH_DENIED", "/etc/shadow")
    key3 = ("bob", "SPK_E_PATH_DENIED", "/etc/passwd")

    # First hit on each key fires.
    assert _should_notify(key1) is True
    assert _should_notify(key2) is True
    assert _should_notify(key3) is True

    # Second hit on key1 within window suppresses.
    assert _should_notify(key1) is False
    # key2 / key3 untouched by key1's suppression.
    assert _should_notify(key2) is False  # already inside window
    assert _should_notify(key3) is False


def test_kind_for_routes_to_correct_family() -> None:
    """Each ErrorCode that should fire a bell maps to the right family."""
    from spark.errors.notify import _kind_for
    from spark.notifications.kinds import NotificationKind

    cases: list[tuple[ErrorCode, NotificationKind | None]] = [
        (ErrorCode.PATH_DENIED, NotificationKind.GATE_FILESYSTEM_DENIED),
        (ErrorCode.URL_DENIED, NotificationKind.GATE_NETWORK_DENIED),
        (ErrorCode.URL_PRIVATE_IP, NotificationKind.GATE_NETWORK_DENIED),
        (ErrorCode.PERMISSION_MISSING, NotificationKind.GATE_PERMISSION_DENIED),
        (ErrorCode.PLUGIN_NOT_ALLOWED, NotificationKind.GATE_PERMISSION_DENIED),
        (ErrorCode.BUDGET_TOOL_EXCEEDED, NotificationKind.GATE_BUDGET_EXCEEDED),
        (ErrorCode.SANDBOX_TIMEOUT, NotificationKind.GATE_SANDBOX_FAILED),
        # Codes we DON'T notify on (caller-side / by-design).
        (ErrorCode.INPUT_SCHEMA_INVALID, None),
        (ErrorCode.FILE_NOT_FOUND, None),
        (ErrorCode.PATH_TRAVERSAL, None),
        (ErrorCode.PLUGIN_RAISED, None),
    ]
    for code, expected in cases:
        actual = _kind_for(code)
        assert actual == expected, f"{code.value} → {actual}, expected {expected}"
