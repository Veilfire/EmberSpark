"""Child-side sandbox worker.

Entrypoint: read one RequestFrame from stdin, import the declared plugin,
instantiate it, await `execute(args, ctx)`, and write one ResponseFrame to
stdout. No stdout-for-logging — errors become structured ResponseFrames.

The worker is intentionally minimal: it imports only the plugin module + the
plugin contract, not the full Spark package, to keep cold-start cost low and
reduce attack surface.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import sys
import traceback
from pathlib import Path
from typing import Any


async def _run() -> int:
    raw = sys.stdin.buffer.read()
    try:
        request = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        _respond(False, error=f"failed to decode request: {exc}", error_type="IPCError")
        return 1

    module_name = request["plugin_module"]
    class_name = request["plugin_class"]
    args = request.get("args") or {}
    secrets = request.get("secrets") or {}
    plugin_config = request.get("plugin_config") or {}
    scratch_path = request.get("scratch_path")
    deliverables_path = request.get("deliverables_path")
    privacy_mode = request.get("privacy_mode") or "strict"

    try:
        module = importlib.import_module(module_name)
        plugin_cls = getattr(module, class_name)
    except Exception as exc:
        _respond(False, error=str(exc), error_type=type(exc).__name__)
        return 1

    try:
        plugin = plugin_cls()
        args_model = plugin.input_schema.model_validate(args)
    except Exception as exc:
        _respond(False, error=str(exc), error_type=type(exc).__name__)
        return 1

    # Build a sandbox-side context. It is a plain dict-with-attrs object; the
    # parent has already filtered permissions, secrets, and plugin config.
    ctx = _WorkerContext(
        secrets=secrets,
        plugin_config=plugin_config,
        scratch_path=Path(scratch_path) if scratch_path else None,
        deliverables_path=Path(deliverables_path) if deliverables_path else None,
        privacy_mode=privacy_mode,
    )

    try:
        result = await plugin.execute(args_model, ctx)
    except Exception as exc:
        # If the plugin raised a SparkError, surface the code + detail
        # across the IPC boundary so the parent can re-raise with full
        # structured context. For anything else, fall back to the generic
        # type name and the parent will wrap as SANDBOX_EXEC_FAILED.
        error_code: str | None = None
        error_detail: dict[str, Any] | None = None
        error_remediation: str | None = None
        try:
            from spark.errors import SparkError  # noqa: PLC0415

            if isinstance(exc, SparkError):
                error_code = exc.code.value
                error_detail = exc.detail
                error_remediation = exc.remediation
        except Exception:  # pragma: no cover — import failure fallback
            pass
        _respond(
            False,
            error=f"{exc}\n{traceback.format_exc(limit=4)}",
            error_type=type(exc).__name__,
            error_code=error_code,
            error_detail=error_detail,
            error_remediation=error_remediation,
        )
        return 1

    try:
        validated = plugin.output_schema.model_validate(result, from_attributes=True)
        result_payload = validated.model_dump(mode="json")
    except Exception as exc:
        _respond(False, error=str(exc), error_type="OutputValidationError")
        return 1

    _respond(True, result=result_payload)
    return 0


class _WorkerContext:
    def __init__(
        self,
        secrets: dict[str, str],
        plugin_config: dict[str, Any] | None = None,
        scratch_path: Path | None = None,
        deliverables_path: Path | None = None,
        privacy_mode: str = "strict",
    ) -> None:
        self.secrets = secrets
        self.privacy_mode = privacy_mode
        self.plugin_config: dict[str, Any] = plugin_config or {}
        self.scratch_path = scratch_path
        self.deliverables_path = deliverables_path


def _respond(
    ok: bool,
    *,
    result: Any = None,
    error: str | None = None,
    error_type: str | None = None,
    error_code: str | None = None,
    error_detail: dict[str, Any] | None = None,
    error_remediation: str | None = None,
) -> None:
    payload = {
        "ok": ok,
        "result": result,
        "error": error,
        "error_type": error_type,
        "error_code": error_code,
        "error_detail": error_detail,
        "error_remediation": error_remediation,
    }
    sys.stdout.buffer.write(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    sys.stdout.buffer.flush()


def main() -> int:
    return asyncio.run(_run())


if __name__ == "__main__":
    raise SystemExit(main())
