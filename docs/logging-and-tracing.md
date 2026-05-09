# Logging, Tracing & Observability

EmberSpark logs to local JSONL files via structlog, emits run-level spans to SQLite, and hash-chains rotated log files for tamper-evidence. There's no cloud sink in v1 — everything stays on disk under `~/.spark/logs/`.

This page is the operational reference for the logging subsystem.

---

## Log format

All events are structlog JSONL. One event per line, UTF-8.

Minimum fields on every event:

```json
{
  "timestamp": "2026-04-13T14:20:01Z",
  "level": "info",
  "event": "tool.invoke",
  "event_type": "tool.invoked",
  "run_id": "run-20260413T142001-abcdef12",
  "task": "weekly-research-digest",
  "agent": "research-assistant",
  "plugin": "http_client",
  "tool_calls": 3
}
```

`run_id`, `task`, and `agent` are bound into structlog's contextvars at the start of every run, so every event emitted inside the run inherits them automatically.

### Event types

Every event carries an `event_type` from the `EventType` enum ([`spark/logging/events.py`](../spark/logging/events.py)). Free-string event types are rejected by the `event_enum_processor` at emit time.

| Category | Event types |
|---|---|
| Task lifecycle | `task.created`, `task.started`, `task.completed`, `task.failed`, `task.rescheduled`, `task.heartbeat`, `task.dlq`, `task.approval_requested` |
| Model / tool | `model.invoked`, `tool.invoked`, `tool.result_received`, `tool.error_classified` |
| Memory | `memory.retrieved`, `memory.promoted` |
| Reflection | `reflection.completed` |
| Plugins | `plugin.loaded`, `plugin.hash_changed` |
| Secrets | `secret.requested` |
| Privacy | `redaction.applied`, `redaction.summary` |
| Scheduler | `scheduler.tick`, `event_trigger.fired`, `webhook.fired` |
| Sandbox | `sandbox.invoked`, `sandbox.denied` |
| Budgets | `budget.exceeded`, `budget.tick` |
| Permissions | `permission.denied` |
| Tracing | `span.emitted`, `prompt.composed` |
| File integrity | `file.header` |

---

## Privacy: the scrub processor

Every event passes through `make_scrub_processor` before serialization. This processor:

1. **Unwraps `SecretStr`** — any `SecretStr` field becomes `"***"`.
2. **Scrubs tracked secret values** — the secret manager tracks every value it's returned, and the scrubber walks the event dict looking for matches and replacing them with `"***"`.
3. **Applies regex patterns** — AWS / OpenAI / Anthropic / GitHub / Slack / Stripe / JWT / PEM / cloud metadata URL patterns are redacted from every string field.
4. **Marks `redaction_applied: true`** when anything changed, so you can audit what the runtime touched.

A typical tool event that had a token in it looks like:

```json
{
  "event_type": "tool.invoked",
  "plugin": "http_client",
  "args_preview": "Bearer ***",
  "redaction_applied": true
}
```

---

## Redaction summaries

Every 60 seconds, the `redaction_stats` aggregator emits a compact summary:

```json
{
  "event_type": "redaction.summary",
  "window_seconds": 60.0,
  "categories": {
    "OPENAI_KEY": 4,
    "JWT": 2,
    "HIGH_ENTROPY": 17
  }
}
```

Counts only — the actual redacted content is **never** included. This gives you compliance-friendly observability: you can see what the runtime is scrubbing without giving up the content.

---

## Correlation IDs + spans

Every run gets a `run_id` and a set of **spans** — parent-child-linked timing records for each engine node and each tool call.

### Span structure

Each span has:

- `id` — autoincrement primary key in `run_spans`
- `run_id` — the owning run
- `parent_span_id` — nullable, links to the parent span in the same run
- `name` — one of `run`, `prepare_context`, `plan`, `tool_call`, `filter_result`, `reflect`, `persist`
- `started_at` / `finished_at`
- `duration_ms`
- `attributes` — JSON dict with context (e.g. `{"plugin": "http_client"}` on a tool_call span)
- `error_class` — set if the span exited via exception

Spans are persisted to the `run_spans` SQLite table and also emitted to the JSONL log as `span.emitted` events for stream-friendly consumers.

### Where spans are instrumented

In [`RuntimeEngine._run_loop`](../spark/runtime/engine.py):

- `run` — the outer span, wraps the whole `_run_loop`
- `prepare_context` — `_retrieve_memory_context` (memory + skills + playbooks)
- `plan` — one per iteration, wraps `_invoke_model`
- `tool_call` — one per `ToolExecutor.call`, attributes include the plugin name

### The flame graph

The web UI's **Run Replay** page (`/runs/{run_id}/replay`) reads spans via `GET /api/replay/{run_id}` and renders them as an SVG flame graph. Clicking a span shows its attributes and error class, if any. Nested spans render as child bars inside their parent's time range.

You can also build your own visualizations — every `span.emitted` event in the JSONL log carries enough structure (duration_ms, parent_span_id, run_id) to feed into any flame-graph tool.

---

## `prompt.composed` events

Every model call emits a `prompt.composed` event immediately before `_invoke_model`:

```json
{
  "event_type": "prompt.composed",
  "iteration": 3,
  "system_chars": 1840,
  "user_chars": 120,
  "tool_history_chars": 4290,
  "memory_count": 5,
  "playbook_id": "pb-summarize-repo"
}
```

**Counts only.** There is no raw content in the event — the point is to let you observe context-window pressure and memory / playbook usage without storing prompts.

If you need raw prompt logging for debugging, flip `logging.raw_prompts: true` in the agent YAML. This requires an explicit edit and writes a `critical`-severity audit entry because it bypasses the privacy defaults.

---

## Tool error classification

Every failing tool call emits a `tool.error_classified` event with an `error_class` field mapping the exception type to one of:

| Class | Triggered by |
|---|---|
| `path_denied` | `PathDenied` from the filesystem plugin |
| `network_denied` | `UrlDenied` from the SSRF defense |
| `budget_exceeded` | `BudgetExceeded` from the BudgetGuard |
| `permission_denied` | `PermissionDenied` from the runtime gate or the plugin itself |
| `sandbox_timeout` | The sandbox child exceeded its wall-clock timeout |
| `sandbox_unavailable` | No sandbox backend available on the host |
| `sandbox_denied` | Subprocess exited with a non-zero code |
| `timeout` | Python `TimeoutError` not tied to the sandbox |
| `plugin_raised` | Any other exception from the plugin's `execute` |

This categorization fuels the Guardrails dashboard, the tool-error classification on run replays, and any alerting you want to wire up downstream.

---

## Budget ticks

Every tick of the BudgetGuard emits `budget.tick` events:

```json
{
  "event_type": "budget.tick",
  "kind": "tool",
  "current": 3,
  "limit": 25
}
```

Three kinds: `iter`, `model`, `tool`. These drive live progress bars in the Overview page and can be consumed by custom tools that want to show in-flight budget pressure.

---

## Retention: hot / warm / cold / archive

Log files rotate daily via Python's `TimedRotatingFileHandler`. On rotation, the old file is placed in a retention bucket based on its age:

| Bucket | Age | Compression |
|---|---|---|
| `hot` | 0–7 days | plain JSONL |
| `warm` | 7–30 days | plain JSONL |
| `cold` | 30–365 days | gzipped |
| `archive` | 365+ days | gzipped, never auto-pruned |

The directory layout under `~/.spark/logs/`:

```
spark.jsonl                # current day
hot/spark.jsonl.2026-04-12
hot/spark.jsonl.2026-04-11
warm/spark.jsonl.2026-04-05.gz
cold/spark.jsonl.2026-03-01.gz
archive/spark.jsonl.2025-04-12.gz
```

The bucketer runs at startup and after every rotation. It's implemented in [`spark/logging/retention.py`](../spark/logging/retention.py).

---

## Hash-chain integrity

Every rotated file's first line is a `file.header` event carrying the sha256 of the previous file:

```json
{
  "event_type": "file.header",
  "timestamp": "2026-04-13T00:00:00Z",
  "prev_sha256": "a7f9c2b1...",
  "version": "1"
}
```

The chain is linear: file N's header contains the hash of file N-1. An attacker who deletes or modifies a rotated file breaks the chain, and the next file's header no longer matches.

### Verifying

```bash
spark logs verify
```

This walks every file in `hot/`, `warm/`, `cold/`, and `archive/` in mtime order, reads the first line, and verifies that `prev_sha256` matches the actual sha256 of the previous file. On success:

```
chain OK — head hash a7f9c2b1deadbeef...
```

On failure:

```
chain broken
  file:     /home/jes/.spark/logs/warm/spark.jsonl.2026-04-05
  expected: b1c4e9...
  actual:   totally-wrong-hash
  reason:   prev_sha256 mismatch — file may have been inserted, removed, or tampered with
```

The command exits 0 on success, 1 on failure. Wire it into a cron if you want periodic integrity checks.

### What the hash chain does NOT do

- It does **not** prevent tampering. An attacker with write access to `~/.spark/logs/` can rewrite every file and recompute the chain.
- It does **not** protect in-memory events. A hostile process running as the EmberSpark user can intercept the JSONL stream before it's written.
- It **does** give you retroactive tamper-evidence: if an attacker forgets to recompute downstream hashes, you'll see the break.

For stronger guarantees, pair the hash chain with:
- Immutable filesystem snapshots (ZFS, Btrfs)
- Offsite periodic copies
- Append-only object storage (S3 with Object Lock)

---

## Tailing logs

Live JSONL stream:

```bash
spark logs tail
```

This watches `~/.spark/logs/spark.jsonl` and pretty-prints each event. The Web UI's **Ops** page has an equivalent live-tail component via SSE at `/api/stream/logs`.

---

## Structured queries

For ad-hoc analysis, use `jq`:

```bash
# All tool errors in the last 24h
find ~/.spark/logs/hot -name "*.jsonl" -mtime -1 \
  | xargs cat \
  | jq 'select(.event_type == "tool.error_classified") | {run_id, plugin, error_class}'

# Redaction hit-rate per category, all time
find ~/.spark/logs -name "*.jsonl*" \
  | xargs zcat -f \
  | jq 'select(.event_type == "redaction.summary") | .categories' \
  | jq -s 'reduce .[] as $x ({}; . as $acc | $x | keys_unsorted | reduce .[] as $k ($acc; .[$k] = (($acc[$k] // 0) + $x[$k])))'

# Every permission denial attributed to a specific run
find ~/.spark/logs -name "*.jsonl*" \
  | xargs zcat -f \
  | jq 'select(.event_type == "permission.denied" and .run_id == "run-20260413T142001-abc")'
```

---

## Opting into raw prompt / output logging

By default:

```yaml
spec:
  logging:
    level: info
    raw_prompts: false
    raw_model_outputs: false
```

You can turn either on, but doing so writes a `critical`-severity audit entry and is **strongly discouraged** outside of short debugging sessions. Raw logs bypass the redaction pipeline — whatever the model saw, whatever the model said, lands in the JSONL file in plaintext.

If you flip them on:

1. Set `raw_prompts: true` and `raw_model_outputs: true` in the agent YAML or the Security Center → Privacy tab.
2. Run the specific task you're debugging.
3. **Flip them off immediately.**
4. Delete any log files that captured the raw content if you don't need the trace anymore.

---

## What's NOT logged

Even with raw logging off, EmberSpark *never* logs:

- Plaintext secret values (unwrapped from `SecretStr` and scrubbed)
- Plaintext passwords (bcrypt-hashed, cleartext discarded after startup display)
- Plaintext API tokens (pattern-scrubbed)
- Plaintext webhook tokens (bcrypt-hashed on creation, cleartext discarded after response)
- Contents of files the agent read (except summarized tool results, which pass through redaction)
- Raw Chroma document text (only the memory_id and metadata)

This is by design. If you need the above, use the web UI to inspect live state — not the log files.

---

## Further reading

- [security-posture.md](security-posture.md) — the full threat model
- [tools-and-permissions.md](tools-and-permissions.md) — how permissions gate tool calls that would otherwise log noisy errors
- [spark/logging/events.py](../spark/logging/events.py) — the complete `EventType` enum
