"""Parent/child IPC frame codec.

Single JSON frame on stdin → single JSON frame on stdout. Secrets travel in the
stdin frame as raw strings (no env vars — `/proc/<pid>/environ` leaks env).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RequestFrame:
    plugin_module: str
    plugin_class: str
    args: dict[str, Any]
    secrets: dict[str, str]
    plugin_config: dict[str, Any] | None = None
    scratch_path: str | None = None
    deliverables_path: str | None = None
    privacy_mode: str = "strict"

    def to_bytes(self) -> bytes:
        return json.dumps(
            {
                "plugin_module": self.plugin_module,
                "plugin_class": self.plugin_class,
                "args": self.args,
                "secrets": self.secrets,
                "plugin_config": self.plugin_config or {},
                "scratch_path": self.scratch_path,
                "deliverables_path": self.deliverables_path,
                "privacy_mode": self.privacy_mode,
            },
            separators=(",", ":"),
        ).encode("utf-8")


@dataclass(frozen=True)
class ResponseFrame:
    ok: bool
    result: Any = None
    error: str | None = None
    error_type: str | None = None
    #: Structured SparkError code emitted by the worker when the plugin
    #: raised a SparkError. None for plain-exception failures.
    error_code: str | None = None
    #: Structured error detail payload (from SparkError.detail). None
    #: for plain-exception failures.
    error_detail: dict[str, Any] | None = None
    #: Optional remediation hint from SparkError.
    error_remediation: str | None = None

    @classmethod
    def from_bytes(cls, data: bytes) -> ResponseFrame:
        try:
            payload = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            return cls(ok=False, error=f"sandbox response not JSON: {exc}", error_type="IPCError")
        if not isinstance(payload, dict) or "ok" not in payload:
            return cls(ok=False, error="sandbox response missing 'ok' key", error_type="IPCError")
        return cls(
            ok=bool(payload["ok"]),
            result=payload.get("result"),
            error=payload.get("error"),
            error_type=payload.get("error_type"),
            error_code=payload.get("error_code"),
            error_detail=payload.get("error_detail"),
            error_remediation=payload.get("error_remediation"),
        )

    def to_bytes(self) -> bytes:
        return json.dumps(
            {
                "ok": self.ok,
                "result": self.result,
                "error": self.error,
                "error_type": self.error_type,
                "error_code": self.error_code,
                "error_detail": self.error_detail,
                "error_remediation": self.error_remediation,
            },
            separators=(",", ":"),
        ).encode("utf-8")
