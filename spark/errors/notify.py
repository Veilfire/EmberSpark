"""Gate-failure notification fan-out.

When a :class:`SparkError` refuses an operation (a permission denial,
budget cap, network/path block, sandbox failure), we want the operator
to see it in the bell + toaster the same day, not on next audit-log
review. This module owns the dispatch.

Design:

* **One :class:`NotificationKind` per gate family**, not per code. A
  tight loop hitting ``PATH_DENIED`` ten times shouldn't ship ten
  different bell rows. The Inspector explains which specific code
  fired.
* **Dedup window per ``(agent, code, target)``**, in-process, 5 minutes.
  Mirrors the existing `_should_notify` helper in
  :mod:`spark.privacy.guardrails`.
* **Best-effort** — fan-out failure must never escalate. The caller's
  error path is the source of truth; this is purely a UX signal.
* **Routes through the existing `NotificationService.notify`** — same
  preference check (per-kind opt-out), same SSE fan-out, same audit
  trail.
"""

from __future__ import annotations

import time
from typing import Any

import structlog

from spark.errors.codes import ErrorCode, SparkError

log = structlog.get_logger("spark.errors.notify")


# 5-minute rolling window. Same as the data-class block dedup.
_NOTIFY_WINDOW_SECONDS = 300.0
_last_notified: dict[tuple[str, str, str], float] = {}


def _should_notify(key: tuple[str, str, str]) -> bool:
    now = time.monotonic()
    last = _last_notified.get(key)
    if last is not None and (now - last) < _NOTIFY_WINDOW_SECONDS:
        return False
    _last_notified[key] = now
    return True


def _kind_for(code: ErrorCode):
    """Map a gate's :class:`ErrorCode` to its notification family.

    Returns ``None`` for codes that are NOT operator-facing gate
    failures — schema validation, FILE_NOT_FOUND, plugin-internal
    raises, etc. Those still flow through audit + run replay; just no
    bell row.

    The return is ``NotificationKind | None``. Import from the leaf
    ``spark.notifications.kinds`` (not the package) so we don't trigger
    the package-init cycle ``spark.notifications.__init__`` →
    ``spark.notifications.service`` → ``spark.web.events`` → the rest
    of the world.
    """
    from spark.notifications.kinds import NotificationKind  # noqa: PLC0415

    if code in (ErrorCode.PLUGIN_NOT_ALLOWED, ErrorCode.PERMISSION_MISSING):
        return NotificationKind.GATE_PERMISSION_DENIED
    if code in (
        ErrorCode.BUDGET_ITER_EXCEEDED,
        ErrorCode.BUDGET_MODEL_EXCEEDED,
        ErrorCode.BUDGET_TOOL_EXCEEDED,
        ErrorCode.BUDGET_TOKEN_EXCEEDED,
        ErrorCode.BUDGET_WALL_CLOCK_EXCEEDED,
    ):
        # COST_HARD_STOP has its own dedicated kind already.
        return NotificationKind.GATE_BUDGET_EXCEEDED
    if code in (
        ErrorCode.URL_DENIED,
        ErrorCode.URL_PRIVATE_IP,
        ErrorCode.URL_METADATA_BLOCKED,
        ErrorCode.METHOD_NOT_ALLOWED,
        ErrorCode.RESPONSE_TOO_LARGE,
    ):
        return NotificationKind.GATE_NETWORK_DENIED
    if code in (
        ErrorCode.PATH_DENIED,
        ErrorCode.FILE_TOO_LARGE,
    ):
        return NotificationKind.GATE_FILESYSTEM_DENIED
    if code in (
        ErrorCode.SANDBOX_TIMEOUT,
        ErrorCode.SANDBOX_UNAVAILABLE,
        ErrorCode.SANDBOX_EXEC_FAILED,
    ):
        return NotificationKind.GATE_SANDBOX_FAILED
    return None


def _target_id(detail: dict[str, Any]) -> str:
    """Best-effort target string for dedup keying.

    Picks the most specific identifier available: host > path > plugin.
    Falls back to ``"_"`` so the dedup still works for raises with no
    structured detail.
    """
    for key in ("host", "hostname", "path", "url", "plugin", "secret_name"):
        if key in detail and detail[key]:
            return str(detail[key])[:64]
    return "_"


def _severity_for(code: ErrorCode) -> str:
    if code in (
        ErrorCode.URL_METADATA_BLOCKED,
        ErrorCode.PATH_TRAVERSAL,
        ErrorCode.PATH_SYMLINK_REFUSED,
    ):
        return "elevated"
    if code in (
        ErrorCode.SANDBOX_UNAVAILABLE,
        ErrorCode.SANDBOX_EXEC_FAILED,
    ):
        return "elevated"
    return "info"


def _title(code: ErrorCode, detail: dict[str, Any]) -> str:
    """Bell-row title — short, identifies the gate + the element."""
    target = _target_id(detail)
    if target == "_":
        return f"Gate refused: {code.value}"
    return f"{code.value} on {target}"


def _action_url(err: SparkError) -> str | None:
    """First (lowest-risk) tuning option's deep link, if any."""
    from spark.errors.remediation import options_for  # noqa: PLC0415

    detail = dict(err.detail)
    detail.setdefault("_message", err.message)
    for opt in options_for(err.code, detail):
        if opt.deep_link:
            return opt.deep_link
    return None


async def notify_gate_failure(
    err: SparkError,
    *,
    agent_name: str | None = None,
    run_id: str | None = None,
) -> None:
    """Fire a `GATE_*` notification for ``err``, deduped per (agent, code, target).

    Idempotent and best-effort — failures inside this helper are logged
    and swallowed so a notification glitch can't cascade into a tool
    error.
    """
    kind = _kind_for(err.code)
    if kind is None:
        return

    # Build the dedup key. ``agent_name`` may be missing for some
    # raises — fall back to detail.agent / "_" so we still dedup.
    agent = (
        agent_name
        or err.detail.get("agent")
        or err.detail.get("agent_name")
        or "_"
    )
    target = _target_id(err.detail)
    key = (str(agent), err.code.value, target)
    if not _should_notify(key):
        return

    body = err.message
    if err.remediation:
        body = f"{body}\n\n{err.remediation}"

    try:
        from spark.notifications import get_notification_service  # noqa: PLC0415

        await get_notification_service().notify(
            kind=kind,
            title=_title(err.code, err.detail)[:200],
            body=body,
            severity=_severity_for(err.code),
            target_kind="gate",
            target_id=f"{err.code.value}:{target}",
            action_url=_action_url(err),
        )
    except Exception as exc:  # pragma: no cover — best effort
        log.warning(
            "gate_notify_failed",
            code=err.code.value,
            error=str(exc),
        )
