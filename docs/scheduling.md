# Scheduling: Events, Chains, Webhooks, DLQ, Approvals

EmberSpark supports four base task modes — **one_shot**, **recurring**, **perpetual**, and **event** — plus a layer of scheduling enhancements: event triggers, chained tasks, dead-letter queues, retry backoff, cost ceilings, approval gates, run-window constraints, heartbeats, webhook triggers, and schedule simulation.

This page is the reference for everything you can put in a task YAML's scheduling block and how each piece composes.

---

## Task modes

Each mode has different rules about which schedule fields are required, allowed, or forbidden. The validator runs at YAML load time and at task-create / task-edit time in the web UI.

| Mode | Schedule | `start_at` | `end_at` |
|---|---|---|---|
| `one_shot` | Optional | Allowed (delays the run) | **Rejected** |
| `recurring` | Required | **Required** | **Required** (`start_at < end_at`) |
| `perpetual` | Required | **Required** | **Rejected** (use `recurring` for finite windows) |
| `event` | **Rejected** (fires from `on:` block) | n/a | n/a |

### `one_shot`

Runs once and exits. The simplest case.

```yaml
apiVersion: spark.veilfire.dev/v1alpha1
kind: Task
metadata:
  name: sync-readme-once
spec:
  agent: research-assistant
  mode: one_shot
  objective: Summarize the GitHub trending list and write a note.
```

Optional delayed-fire form — `start_at` is allowed, `end_at` is rejected:

```yaml
spec:
  agent: research-assistant
  mode: one_shot
  schedule:
    type: cron
    expression: "0 8 * * 1"
    timezone: America/Vancouver
    start_at: "2026-06-01T00:00:00+00:00"   # fires once, on or after this date
  objective: One-time summary at the start of June.
```

### `recurring`

Runs on a cron or interval schedule **inside a finite window**. Both `start_at` and `end_at` are required, with `start_at < end_at`. APScheduler honors both endpoints — fires nothing before `start_at`, removes the job after `end_at`.

```yaml
spec:
  agent: research-assistant
  mode: recurring
  schedule:
    type: cron
    expression: "0 8 * * 1"
    timezone: America/Vancouver
    start_at: "2026-06-01T00:00:00+00:00"
    end_at:   "2026-09-01T00:00:00+00:00"
```

Interval form:

```yaml
  schedule:
    type: interval
    seconds: 3600
    timezone: UTC
    start_at: "2026-06-01T00:00:00+00:00"
    end_at:   "2026-09-01T00:00:00+00:00"
```

If you want an unbounded recurring task, use `mode: perpetual` and drop `end_at`. The validator refuses `mode: recurring` without an explicit window.

### `perpetual`

A standing responsibility — wakes on a schedule, does its work, goes back to sleep. **Unbounded:** `start_at` is required but `end_at` is rejected. Optionally emits heartbeats for liveness detection.

```yaml
spec:
  agent: watchdog
  mode: perpetual
  schedule:
    type: interval
    seconds: 300
    start_at: "2026-01-01T00:00:00+00:00"   # required for perpetual
  heartbeat_seconds: 60
```

**Why `start_at` is required:** the contract is "explicit kickoff, no end." A past timestamp (like `2026-01-01`) means "fire on the next cron tick from now." A future timestamp delays the first run.

A watchdog checks for missed heartbeats: if the task skips more than 2× `heartbeat_seconds` without emitting one, the scheduler flips it to `failed`.

### `event`

Fired from an external trigger, not the cron scheduler. The `schedule` block is rejected; the `on:` block (`file_changed` or `http_new_row`) drives firing — see the next section.

---

## Event triggers

A task can be fired on an external event instead of (or in addition to) a cron schedule. Add an `on:` block to the task spec.

### File-changed trigger

```yaml
spec:
  agent: inbox-watcher
  mode: event
  on:
    type: file_changed
    path: ~/Documents/inbox
    recursive: true
    debounce_seconds: 5
```

Implementation: a `watchdog.Observer` watches `path`. When events arrive, they queue up for `debounce_seconds` and then fire once with a `changes` payload carrying up to 50 changed paths. Rapid bursts collapse to a single fire.

### HTTP new-row trigger

```yaml
spec:
  agent: rss-poller
  mode: event
  on:
    type: http_new_row
    url: https://api.example.com/feed
    allow_hosts:
      - api.example.com
    poll_seconds: 300
    key_path: id
```

The trigger polls `url` every `poll_seconds`, parses the JSON body as a list (or `items` array), and dedupes by the JSON path at `key_path`. The first poll establishes the baseline — new rows on subsequent polls fire the task with the new items as the payload.

`allow_hosts` is enforced against the same SSRF defense as the `http_client` plugin — the URL is IDN-normalized, DNS-pinned, and the resolved IP is checked against the private / loopback / metadata blocklist.

---

## Chained tasks

A task can declare a successor to fire on success or failure:

```yaml
spec:
  agent: researcher
  mode: recurring
  schedule: { type: cron, expression: "0 8 * * 1" }
  on_success: weekly-report-publisher
  on_failure: weekly-report-fallback
```

When the engine finishes a run:

- If `state == completed` and `on_success` is set, the lifecycle manager marks that task as `scheduled` so the scheduler fires it on its next tick.
- If `state == failed` and `on_failure` is set, same thing for the failure successor.

Depth is capped at **5** chained fires to prevent infinite loops. A chain that exceeds the cap is audited and halted.

---

## Retry policy and backoff

```yaml
spec:
  retry:
    max_attempts: 3
    backoff_seconds: 5.0
    backoff_multiplier: 2.0
    jitter_seconds: 2.0
```

- After a failed run, the scheduler computes `delay = backoff_seconds * backoff_multiplier^(attempt-1) + uniform(0, jitter_seconds)`.
- Delay is capped at **3600 seconds** (1 hour) regardless of multiplier.
- `max_attempts=3` means the runtime will try up to 3 times total — 1 initial + 2 retries.
- After `max_attempts` failures in a row, the task moves to the **dead-letter queue** (see below).

---

## Dead-letter queue

When a recurring task fails `max_attempts` consecutive times, the scheduler transitions it to state `dlq` and stops firing it. You see it in the Scheduler page with a red DLQ chip, and it appears in the Guardrails dashboard.

To re-enable:

1. Figure out *why* it was failing. Check the run history for the error, look at the latest run's trace, check cost events, confirm the target is still reachable.
2. Fix the underlying issue.
3. Click **Ack DLQ** on the task in the Scheduler UI (or `POST /api/scheduler/dlq/{task_name}/ack`). This resets `consecutive_failures` and puts the task back in `scheduled` state.
4. The next scheduled fire runs normally.

---

## Cost ceilings per trigger

Before each fire, the scheduler calls `check_budgets(agent, provider)` from the cost tracker. If any active budget (global / per-agent / per-provider) has been exceeded for the current period, the fire is **deferred** — the task stays scheduled but doesn't run until the next period or until the budget is raised.

Budgets are configured in the Web UI's Cost page. A typical setup:

- Monthly global ceiling: $100 hard stop
- Daily per-provider soft alert on anthropic: $5

A soft-alert trip fires the task anyway with a yellow warning in the audit log. A hard-stop trip defers with an `elevated` audit entry.

---

## Approval gate

```yaml
spec:
  approval:
    required: true
    note: "Must be reviewed before any spending."
```

When set, every fire of this task lands in state `awaiting_approval` instead of running. The Scheduler page lists pending approvals; click **Approve** to release a specific fire to the runtime.

Under the hood, `Lifecycle.run_once` checks `task.spec.approval.required` before dispatching. If true, it emits a `task.approval_requested` event, sets the task state to `paused`, and raises `PermissionDenied` so the scheduler re-queues rather than executing.

Approved fires are audited at `elevated` severity with the approver's subject in the actor field.

---

## Run-window constraints

If a task should only run during specific hours:

```yaml
spec:
  only_between: "22:00-06:00 America/Vancouver"
```

The format is `HH:MM-HH:MM TZ`. Windows that cross midnight are supported (e.g. `22:00-06:00`).

The check happens in `Lifecycle.run_once` before the engine starts. Outside the window, the fire raises `PermissionDenied` with a clear message and is audited.

This is useful for:

- Overnight batch jobs that shouldn't fight interactive chat for budget
- Respecting quiet hours for notification-sending tasks
- Running in a cheaper-electricity timezone window if you care about that

---

## Webhook triggers

Webhook triggers fire a task when a signed HTTP call arrives. Two auth modes:

- **`bearer`** — caller sends `X-Spark-Token: <token>`. Token is bcrypt-hashed on disk. Good for hand-rolled scripts.
- **`hmac_sha256`** — caller sends a body and `X-Hub-Signature-256: sha256=<hex>` (or `X-Slack-Signature` / `X-Spark-Signature-256`). Shared secret stored in the age vault under `webhook.trigger.<id>.hmac_secret`. Required for **GitHub, Slack**, and most modern signed-webhook providers.

Triggers can also:

- **Forward the request body** to the task — lands on `RunState.trigger_payload` and is rendered into the planner's first system prompt (capped at 32 KB; full body persisted on the run row as `trigger_payload_json`).
- **Filter inbound events** — a JSON object of dotted-path → expected-value rules. Trigger fires the task only when every rule matches.

### Creating a webhook

Only **admins** can create webhooks. In the Scheduler UI (**+ New trigger**), or:

```bash
curl -X POST \
  -H "X-Spark-Token: $(cat ~/.spark/web-token)" \
  -H "Content-Type: application/json" \
  -d '{
    "trigger_id": "github-pr-merge",
    "task_name": "code-review-on-merge",
    "auth_mode": "hmac_sha256",
    "payload_forwarding": true,
    "event_filter": {"action": "closed", "pull_request.merged": true},
    "rate_limit_per_hour": 60
  }' \
  http://127.0.0.1:7777/api/scheduler/triggers
```

The response includes the **cleartext credential** exactly once:

```json
{
  "trigger_id": "github-pr-merge",
  "task_name": "code-review-on-merge",
  "auth_mode": "hmac_sha256",
  "secret": "yn2K_FEsk3AlN..."
}
```

**Save this immediately.** Bearer tokens are bcrypt-hashed on disk; HMAC secrets are stored cleartext in the age vault (necessary because verification needs the original to recompute the digest). Either way, you cannot retrieve it after creation.

### Firing a webhook

**Bearer mode:**

```bash
curl -X POST \
  -H "X-Spark-Token: yn2K_FEsk3AlN..." \
  -H "Content-Type: application/json" \
  -d '{"hello": "world"}' \
  http://127.0.0.1:7777/api/scheduler/webhooks/github-pr-merge
```

**HMAC mode (GitHub):**

```bash
BODY='{"action":"closed","pull_request":{"merged":true,"number":42}}'
SIG=$(printf '%s' "$BODY" | openssl dgst -sha256 -hmac "yn2K_FEsk3AlN..." | awk '{print $2}')
curl -X POST \
  -H "Content-Type: application/json" \
  -H "X-Hub-Signature-256: sha256=$SIG" \
  -d "$BODY" \
  http://127.0.0.1:7777/api/scheduler/webhooks/github-pr-merge
```

The endpoint:

1. Looks up the trigger by ID.
2. Verifies the credential (bcrypt for bearer; HMAC-SHA256 for hmac_sha256). Constant-time. Bad sig increments `failed_verify_count`; 10 in a row → trigger locked for 15 min.
3. Enforces the per-trigger rate limit.
4. Applies the event filter (if set). Body must match every rule or the response is `{"status": "filtered"}` (200, no fire).
5. Calls `execute_task_by_name` directly — the task runs immediately, payload (if forwarded) on `RunState.trigger_payload`.
6. Writes an audit entry (`trigger.fired` at `info` severity) and increments `fires_total`.
7. Returns `{"status": "scheduled", "task_name": "..."}`.

Status codes: 401 bad credential / locked, 404 unknown trigger, 429 rate limit, 503 vault locked (HMAC mode only).

### Deleting a webhook

```bash
curl -X DELETE \
  -H "X-Spark-Token: $(cat ~/.spark/web-token)" \
  http://127.0.0.1:7777/api/scheduler/triggers/github-pr-merge
```

Writes an `elevated` audit entry, removes the row, and (for HMAC triggers) deletes the vault secret. The old credential becomes invalid immediately.

### Slack integration

Slack's Events API uses the same HMAC-SHA256 scheme as GitHub, just with `X-Slack-Signature` instead of `X-Hub-Signature-256`. Create a trigger with `auth_mode: hmac_sha256`, paste the cleartext secret as Slack's **Signing Secret**, and set the Event Subscription URL to `https://your-host/api/scheduler/webhooks/<trigger_id>`. Use an event filter like `{"event.type": "app_mention"}` to fire only on @-mentions of your bot.

### Telegram bot trigger

Telegram uses long-poll `getUpdates`, not webhooks (avoids needing a public TLS endpoint). Configure via the `event` task mode:

```yaml
spec:
  agent: chat-responder
  mode: event
  on:
    type: telegram_message
    bot_token_secret: telegram_bot_token
    allow_chat_ids: [123456789]
```

Set the bot token first via `spark secrets set telegram_bot_token`. The scheduler long-polls Telegram and fires the task per message in a whitelisted chat. **Always set `allow_chat_ids`** in production — empty means any chat that DMs your bot can fire tasks.

---

## Schedule simulation

Before you commit a cron expression, you can ask EmberSpark to show you exactly when it would fire:

```bash
curl -X POST \
  -H "X-Spark-Token: $(cat ~/.spark/web-token)" \
  -H "Content-Type: application/json" \
  -d '{
    "schedule_type": "cron",
    "expression": "0 8 * * 1",
    "timezone": "America/Vancouver",
    "horizon_hours": 336
  }' \
  http://127.0.0.1:7777/api/scheduler/simulate
```

Returns:

```json
{
  "count": 2,
  "fires": [
    "2026-04-13T15:00:00+00:00",
    "2026-04-20T15:00:00+00:00"
  ]
}
```

Nothing is persisted. It's a pure calculation against the APScheduler trigger iterator — useful for sanity-checking cron expressions, especially around DST boundaries and unusual `*/n` patterns.

Interval form:

```json
{
  "schedule_type": "interval",
  "expression": "3600",
  "timezone": "UTC",
  "horizon_hours": 24
}
```

---

## Task states

Tasks move through a small state machine:

```
created → scheduled ⇄ running
                ↓       ↓
              paused  completed / failed
                ↓       ↓
              scheduled   dlq (after N consecutive failures)
```

- `created` — just registered, never fired
- `scheduled` — on the APScheduler job store, waiting for its next tick
- `running` — currently executing in the engine
- `paused` — approval gate or manual pause; won't fire until resumed
- `completed` — last run succeeded
- `failed` — last run failed; retry may or may not be scheduled depending on `retry.max_attempts`
- `dlq` — hit `max_attempts` consecutive failures; won't fire until operator acks
- `stopped` — explicitly stopped; must be manually restarted
- `sleeping` — perpetual task between wake cycles

State transitions are checked by `assert_transition` in [`lifecycle.py`](../spark/runtime/lifecycle.py). Invalid transitions raise `InvalidTransition` and don't land.

---

## A complete example

Put it all together — a recurring research digest task with retries, DLQ, approval, run window, and a webhook backup:

```yaml
apiVersion: spark.veilfire.dev/v1alpha1
kind: Task
metadata:
  name: weekly-research-digest

spec:
  agent: research-assistant
  mode: recurring

  schedule:
    type: cron
    expression: "0 8 * * 1"
    timezone: America/Vancouver

  objective: >
    Gather new research notes from configured sources, summarize key findings,
    extract action items, and write a markdown digest.

  session:
    name: weekly-research
    continuity: bounded

  output:
    type: file
    path: ~/Documents/spark-workspace/digests/weekly.md

  budgets:
    max_runtime_seconds: 900
    max_model_calls: 30
    max_tool_calls: 25

  retry:
    max_attempts: 3
    backoff_seconds: 60.0
    backoff_multiplier: 2.0
    jitter_seconds: 30.0

  approval:
    required: false

  only_between: "08:00-22:00 America/Vancouver"

  on_success: weekly-digest-announcer
  on_failure: weekly-digest-alert
```

Then in the Scheduler UI, create a webhook so an external cron can also trigger this task if needed. The webhook + the cron fire independently — they just both push the task into `scheduled` state, and the scheduler runs it once per entry into that state.

---

## Further reading

- [tools-and-permissions.md](tools-and-permissions.md) — how scheduling composes with the permission gates
- [logging-and-tracing.md](logging-and-tracing.md) — span events and run replay for debugging failed schedules
- [security-posture.md](security-posture.md) — webhook auth, trigger token handling, DLQ as a defense-in-depth
