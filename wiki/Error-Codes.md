# Error Codes

When a tool call fails, EmberSpark serializes the failure into a stable
`ErrorCode` enum value so the planner can branch on a stable identifier
instead of parsing English. The code travels with a human message,
structured detail, and an actionable remediation hint.

The first time a model sees one of these errors, the engine appends a
one-shot reference card to the system prompt so the planner learns the
vocabulary.

## Payload the model sees

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

- `code` — stable across releases, prefixed `SPK_E_`
- `message` — short, one line, human readable
- `detail` — structured context (plugin name, offending value, allowlist, etc.)
- `remediation` — short actionable hint

## Code families

| Family | Examples | What trips it |
|---|---|---|
| **Plugin allowlist** | `SPK_E_PLUGIN_NOT_ALLOWED`, `SPK_E_PLUGIN_NOT_REGISTERED` | Agent tried to call a plugin it wasn't granted |
| **Permission grants** | `SPK_E_PERMISSION_MISSING` | Plugin needed `net.http` and the agent didn't have it |
| **Budgets** | `SPK_E_BUDGET_ITER_EXCEEDED`, `SPK_E_BUDGET_MODEL_EXCEEDED`, `SPK_E_BUDGET_TOOL_EXCEEDED`, `SPK_E_BUDGET_TOKEN_EXCEEDED`, `SPK_E_BUDGET_WALL_CLOCK_EXCEEDED`, `SPK_E_BUDGET_COST_HARD_STOP` | Iteration, model-call, tool-call, **token-count**, wall-clock, or cost ceiling hit. Token budget = sum of input + output tokens across all model calls in the run; opt-in via `runtime.max_tokens_per_run`. |
| **Validation** | `SPK_E_INPUT_SCHEMA_INVALID`, `SPK_E_OUTPUT_SCHEMA_INVALID`, `SPK_E_OPERATOR_OVERRIDE_REFUSED` | Tool args don't match the schema, or the model tried to override a locked operator field |
| **Sandbox** | `SPK_E_SANDBOX_UNAVAILABLE`, `SPK_E_SANDBOX_TIMEOUT`, `SPK_E_SANDBOX_EXEC_FAILED` | No backend, child process hit the wall-clock timeout, or sandbox infrastructure failed |
| **Network** | `SPK_E_URL_DENIED`, `SPK_E_URL_METADATA_BLOCKED`, `SPK_E_URL_PRIVATE_IP`, `SPK_E_URL_IDN_INVALID`, `SPK_E_METHOD_NOT_ALLOWED`, `SPK_E_RESPONSE_TOO_LARGE` | SSRF gauntlet refused the URL, method outside the per-host allowlist, response too big |
| **Filesystem** | `SPK_E_PATH_DENIED`, `SPK_E_PATH_TRAVERSAL`, `SPK_E_PATH_SYMLINK_REFUSED`, `SPK_E_FILE_NOT_FOUND`, `SPK_E_FILE_TOO_LARGE` | Path outside allow list, `..` traversal, symlink, missing, or too big |
| **Secrets** | `SPK_E_SECRET_NOT_FOUND`, `SPK_E_SECRET_PROVIDER_UNAVAILABLE` | Name not in vault + env fallback, or the age vault isn't initialized |
| **Runtime** | `SPK_E_FROZEN`, `SPK_E_APPROVAL_REQUIRED`, `SPK_E_RUN_WINDOW_CLOSED`, `SPK_E_DLQ_UNACKED` | Global posture, approvals, schedule windows, dead-letter queue |
| **Data Classification** | `SPK_E_DATA_CLASS_BLOCKED`, `SPK_E_DATA_CLASS_GRANT_REQUIRED` | A guardrail refused content; see [Data Classification Guardrails](Data-Classification-Guardrails) |
| **Plugin-internal** | `SPK_E_PLUGIN_RAISED` | Plugin raised a plain exception — check the plugin's logs |

For the full table with remediations, see
[docs/error-codes.md](../docs/error-codes.md) in the source tree.

## Using codes in your automation

Every structured error lands in the `tool.error_classified` log event
with the code as a first-class field — so `jq` across the JSONL log
works directly:

```bash
jq 'select(.error_code == "SPK_E_PERMISSION_MISSING") | {ts, plugin, detail}' \
   ~/.spark/logs/hot/spark.jsonl
```

Use this to answer questions like "which plugins are hitting permission
errors this week" or "which agent is burning through its tool budget" —
without parsing free-form English.

## Related

- [Permissions Guide](Permissions-Guide)
- [Concept: Permissions](Concepts-Permissions)
- [Concept: Budgets](Concepts-Budgets)
- [Concept: Sandbox](Concepts-Sandbox)
- [Logging & Tracing](Logging-And-Tracing)
