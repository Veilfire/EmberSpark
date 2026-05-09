# Logging & Tracing

For the source-level reference, see [docs/logging-and-tracing.md](https://github.com/Veilfire/EmberSpark/blob/main/docs/logging-and-tracing.md). This wiki page is the operator summary.

## Where things live

- **JSONL log files** — `~/.spark/logs/spark.jsonl` (current) + rotated files in `hot/`, `warm/`, `cold/`, `archive/` subdirectories
- **Span records** — `run_spans` table in `~/.spark/spark.db`
- **Audit log** — `audit_log` table in `~/.spark/spark.db`

The JSONL files are the fire hose — every runtime event. The span table is the structured view for the flame graph. The audit log is the security-relevant-mutations-only paper trail.

## The four things you'll actually do

### 1. Tail logs live

```bash
spark logs tail
```

Or the **Ops** page in the UI → live log tail section. Streams new events as they land via Server-Sent Events at `/api/stream/logs`. Backfills the last 50 lines on connect so the panel populates immediately even on an idle daemon.

The same SSE bus also fans out structured runtime events (notification creation, scheduler ticks, tool invocations) at `/api/stream/events`. Both endpoints emit **unnamed** SSE messages (only a `data:` line, no `event:` prefix) so a standard `EventSource.onmessage` handler picks them up. The event-name lives inside the JSON envelope's top-level `kind` field — for notification fan-out the per-row category is under `payload.notification_kind` (separate from the envelope `kind` to avoid colliding with the bus's positional kwarg).

### 2. Verify hash-chain integrity

```bash
spark logs verify
```

Walks every rotated log file in age order and verifies that each file's `file.header` event carries the correct `prev_sha256`. A broken chain means a file was inserted, removed, or modified — possibly by you accidentally, possibly not.

Exits 0 on success, 1 on failure with a specific error pointing at the broken file.

Run this as a nightly cron if you want periodic integrity checks.

### 3. Read the run replay flame graph

Click any run_id in the **Runs** page to go to the Run Replay page. The flame graph shows every span (engine node + tool call) with duration, parent linkage, and error class if it failed.

Use this when:

- A run took longer than expected — find the slow span
- A run failed — find the span with the red error class
- You're debugging why the agent made a particular choice — the `prompt.composed` events near a model span show the exact context shape

### 4. Filter the audit log

**Audit Log** page in the UI. Filters:

- **Kind** — substring match (e.g. `permission_denied`, `plugin.config.update`, `persona.activated`)
- **Severity** — `info` / `elevated` / `critical`

Use this for:

- "When did I last widen this allowlist?"
- "Who approved that skill?"
- "What was the freeze reason last week?"

## Retention and compression

Log files rotate daily. After rotation they're placed in a bucket based on age:

| Bucket | Age | Storage |
|---|---|---|
| `hot` | 0–7 days | plain JSONL |
| `warm` | 7–30 days | plain JSONL |
| `cold` | 30–365 days | gzipped |
| `archive` | 365+ days | gzipped, never auto-pruned |

The bucketer runs at startup and on each rotation. Compression is automatic when a file enters `cold` or `archive`.

You can query `.gz` files with `zcat` + `jq`:

```bash
zcat ~/.spark/logs/cold/spark.jsonl.2026-01-15.gz | \
  jq 'select(.event_type == "permission.denied")'
```

## Raw prompt / output logging

By default, raw prompt and raw model output logging is **off**. Turn it on only when debugging a specific issue.

To enable:

1. Edit the agent YAML — set `logging.raw_prompts: true` and `logging.raw_model_outputs: true`
2. OR use the Security Center → Privacy tab (requires double confirmation)
3. EmberSpark writes a `critical` audit entry immediately
4. Run the specific task you're debugging
5. **Turn it off again** by reverting the flags

While raw logging is on, the redaction pipeline is bypassed — whatever the model saw and whatever it said lands in the JSONL file in plaintext. Don't leave this on. See [docs/security-posture.md](https://github.com/Veilfire/EmberSpark/blob/main/docs/security-posture.md) for why.

## Correlation IDs + spans at a glance

Every run:

- Gets a `run_id` like `run-20260413T142001-abcdef12`
- Binds that into structlog's context so every subsequent event in the run has it
- Produces a tree of **spans**: `run → prepare_context → plan → tool_call → plan → tool_call → ...`
- Emits a `span.emitted` event per span with `duration_ms`, `parent_span_id`, and `error_class` (if any)

The web UI's Replay page reads the `run_spans` table and renders the tree as a flame graph. The JSONL log carries the same data as individual events.

## What's NOT logged

Even with raw logging off:

- **Plaintext secret values** — unwrapped from SecretStr and scrubbed by structlog
- **Plaintext passwords** — bcrypt-hashed; cleartext discarded after startup display
- **Plaintext API tokens** — pattern-scrubbed (AWS, OpenAI, Anthropic, JWT, GitHub, Slack, Stripe)
- **Plaintext webhook tokens** — bcrypt-hashed on creation
- **Contents of files the agent read** — only summarized tool results, which pass through redaction
- **Chroma document text** — only memory_id and metadata

This is deliberate. If you need to see any of the above, you need to explicitly enable raw logging temporarily — which is audited.

## Further reading

- [docs/logging-and-tracing.md](https://github.com/Veilfire/EmberSpark/blob/main/docs/logging-and-tracing.md) — full reference
- [Concepts: Privacy](Concepts-Privacy) — the redaction pipeline
- [Security Center Guide](Security-Center-Guide) → Privacy tab — how to enable raw logging
