# Error Codes Reference

EmberSpark classifies every failure into a stable `ErrorCode` enum value.
When a tool call fails, the engine serializes the code + message +
structured `detail` + remediation hint into the next model message so
the planner can branch on a stable identifier instead of parsing
English.

## The payload shape

Every tool error reaches the model as:

```json
{
  "role": "tool",
  "name": "http_tool",
  "content": {
    "error": {
      "code": "SPK_E_METHOD_NOT_ALLOWED",
      "message": "method 'POST' not allowed on 'api.github.com'",
      "detail": {
        "plugin": "http_tool",
        "method": "POST",
        "host": "api.github.com",
        "allowed": ["GET"]
      },
      "remediation": "Ask the operator to add the HTTP method to the plugin's per-host rule."
    }
  }
}
```

The `code` field is stable across releases. `message` is human-readable.
`detail` carries structured context. `remediation` is an actionable hint
the model can follow without additional prompt engineering.

## Full code reference

### Layer 1 — Plugin allowlist

| Code | Meaning | Remediation |
|---|---|---|
| `SPK_E_PLUGIN_NOT_ALLOWED` | Plugin not in agent's `spec.plugins.allow` | Add to the allowlist |
| `SPK_E_PLUGIN_NOT_REGISTERED` | Plugin not in the runtime registry | Install the plugin package |

### Layer 2 — Permission grants

| Code | Meaning | Remediation |
|---|---|---|
| `SPK_E_PERMISSION_MISSING` | Agent lacks a required permission | Add the missing grant to `spec.permissions.grants` |

### Layer 3 — Budgets

| Code | Meaning | Remediation |
|---|---|---|
| `SPK_E_BUDGET_ITER_EXCEEDED` | Too many planner loop iterations | Raise `runtime.max_iterations` or investigate looping |
| `SPK_E_BUDGET_MODEL_EXCEEDED` | Too many LLM calls | Raise `runtime.max_model_calls` or trim scope |
| `SPK_E_BUDGET_TOOL_EXCEEDED` | Too many tool invocations | Raise `runtime.max_tool_calls` |
| `SPK_E_BUDGET_TOKEN_EXCEEDED` | Sum of prompt + completion tokens exceeded `runtime.max_tokens_per_run` | Raise the cap, trim prompt context, or unset (null = unbounded) |
| `SPK_E_BUDGET_WALL_CLOCK_EXCEEDED` | Hit `max_runtime_seconds` | Raise the timeout or speed up the operation |
| `SPK_E_BUDGET_COST_HARD_STOP` | Cost budget tripped | Wait for period reset, raise the limit, or delete budget |

### Layer 4 — Input/output validation

| Code | Meaning | Remediation |
|---|---|---|
| `SPK_E_INPUT_SCHEMA_INVALID` | Model args don't match the plugin's `input_schema` | Send only fields in `input_schema` |
| `SPK_E_OUTPUT_SCHEMA_INVALID` | Plugin returned something that doesn't match its `output_schema` | Plugin bug — file an issue |
| `SPK_E_OPERATOR_OVERRIDE_REFUSED` | Operator config locked a field the model tried to override | Stop trying to override the locked field |

### Layer 5 — Sandbox

| Code | Meaning | Remediation |
|---|---|---|
| `SPK_E_SANDBOX_UNAVAILABLE` | No sandbox backend | Install bubblewrap/nsjail (Linux) or confirm sandbox-exec (macOS) |
| `SPK_E_SANDBOX_TIMEOUT` | Child process exceeded wall-clock limit | Raise `permissions.sandbox.timeout_seconds` |
| `SPK_E_SANDBOX_EXEC_FAILED` | Sandbox infrastructure failure (spawn, IPC, etc.) | Often transient — retry |

### Network (SSRF defense)

| Code | Meaning | Remediation |
|---|---|---|
| `SPK_E_URL_DENIED` | URL not in the operator's allowlist | Add the host to `rules` / `allow_hosts` |
| `SPK_E_URL_METADATA_BLOCKED` | URL resolved to a cloud metadata IP | Not reachable — by design |
| `SPK_E_URL_PRIVATE_IP` | URL resolved to RFC1918/loopback/link-local | Use public DNS or request an internal-IP grant |
| `SPK_E_URL_IDN_INVALID` | Hostname could not be IDN-normalized | Use the punycode form |
| `SPK_E_METHOD_NOT_ALLOWED` | HTTP method not in the per-host rule | Add to the rule's `allowed_methods` |
| `SPK_E_RESPONSE_TOO_LARGE` | Response exceeded `max_response_bytes` | Paginate or raise the cap |

### Filesystem

| Code | Meaning | Remediation |
|---|---|---|
| `SPK_E_PATH_DENIED` | Path outside `allow_paths` / inside `deny_paths` | Add to `allow_paths` |
| `SPK_E_PATH_TRAVERSAL` | Path contained `..` | Use absolute paths inside the workspace |
| `SPK_E_PATH_SYMLINK_REFUSED` | Symlink in an otherwise-allowed path | Resolve the symlink at the operator level |
| `SPK_E_FILE_NOT_FOUND` | File does not exist | Verify the path |
| `SPK_E_FILE_TOO_LARGE` | File exceeds `max_read_bytes` | Raise the cap or read in chunks |

### Secrets

| Code | Meaning | Remediation |
|---|---|---|
| `SPK_E_SECRET_NOT_FOUND` | Secret name not in the vault or env fallback | `spark secrets set <name>` |
| `SPK_E_SECRET_PROVIDER_UNAVAILABLE` | Age vault not initialized | `spark secrets init-age-vault` |

### Runtime (task lifecycle)

| Code | Meaning | Remediation |
|---|---|---|
| `SPK_E_FROZEN` | Global posture is frozen | Unfreeze via Security Center → Global Posture |
| `SPK_E_APPROVAL_REQUIRED` | Task paused awaiting operator approval | Approve in Scheduler page |
| `SPK_E_RUN_WINDOW_CLOSED` | Task outside its configured run window | Wait for the window to open |
| `SPK_E_DLQ_UNACKED` | Task is in the dead-letter queue | Ack the task to re-enable it |

### Plugin-internal

| Code | Meaning | Remediation |
|---|---|---|
| `SPK_E_PLUGIN_RAISED` | Plugin raised a plain exception (no structured code) | Check the plugin's logs |

## How to raise a structured error from plugin code

```python
from spark.errors import ErrorCode, SparkError

raise SparkError(
    code=ErrorCode.METHOD_NOT_ALLOWED,
    message=f"method {method!r} not allowed on {host!r}",
    detail={"plugin": "http_tool", "method": method, "host": host, "allowed": list(allowed)},
    remediation="Ask the operator to add the method to the per-host rule.",
)
```

The `SparkError` propagates across the sandbox boundary via
`ResponseFrame.error_code`/`error_detail`/`error_remediation`; the parent
re-raises on the calling side with the same code and detail.

Legacy `raise PermissionError("...")` and `raise PermissionDenied("...")`
still work — they surface as `SPK_E_PLUGIN_RAISED` (default) or
`SPK_E_PERMISSION_MISSING` (the `PermissionDenied` default).

## Logging

Every structured error lands in the `tool.error_classified` log event
with the code as a first-class field:

```json
{
  "event": "tool.error_classified",
  "event_type": "tool.error",
  "plugin": "http_tool",
  "error_code": "SPK_E_METHOD_NOT_ALLOWED",
  "detail": { ... }
}
```

This makes `jq`-based log analysis straightforward:

```bash
jq 'select(.error_code == "SPK_E_PERMISSION_MISSING")' ~/.spark/logs/hot/spark.jsonl
```
