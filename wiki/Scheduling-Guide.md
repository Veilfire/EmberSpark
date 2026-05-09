# Scheduling Guide

This wiki page is the operator overview for task scheduling. For every field and the full YAML reference, see [docs/scheduling.md](https://github.com/Veilfire/EmberSpark/blob/main/docs/scheduling.md).

---

## The four task modes

| Mode | When to use | Schedule constraints |
|---|---|---|
| `one_shot` | Fire once and exit. Simplest. Good for ad-hoc jobs. | Schedule optional. `start_at` allowed for delayed runs. `end_at` rejected. |
| `recurring` | Cron or interval inside a **finite window** — campaigns, summer-only digests, fixed-term polling. | Schedule + `start_at` + `end_at` all required. `start_at < end_at`. |
| `perpetual` | A standing responsibility that wakes on schedule, acts, sleeps. Emits heartbeats. **Unbounded.** | Schedule + `start_at` required. `end_at` rejected (use `recurring` for a finite window). |
| `event` | Triggered by a file change or an HTTP new-row poller. | Schedule rejected — fires from external triggers. |

All four can be chained together with `on_success` / `on_failure`, gated by approval, constrained to run windows, protected by retry + DLQ policies, and fired externally via webhook triggers.

The mode-aware schedule rules are enforced both by the YAML loader and by the web UI's task creator. Bad combinations fail validation with a clear error.

### Creating tasks from the web UI

The Scheduler page has a **+ New task** button that opens a modal-driven creator:

- Pick the agent + mode (radio).
- The schedule block expands or hides based on mode (one-shot doesn't show end_at; perpetual hides end_at; recurring shows both).
- The cron expression is built via a **visual preset picker** with 8 options (every-N-minutes, every-N-hours, daily, every-weekday, weekly, monthly, yearly, custom). Generated cron string is shown read-only.
- Live "Next 5 fires" preview reuses `/api/scheduler/simulate`.
- Optional collapsibles for budget overrides + forensic capture.
- "Auto-start" checkbox registers the task with the scheduler immediately.

Existing tasks have an **Edit** button that reuses the same modal — pre-populated, with `name` locked. Edits while a run is in flight return 409. Changing the agent on an existing task is allowed but audited at `elevated` severity.

---

## The scheduling layers

Like permissions, scheduling is a composition. A task can have:

1. **A base trigger** — cron, interval, event, or webhook
2. **A retry policy** — how many times to retry on failure, with exponential backoff + jitter
3. **A dead-letter queue** — after N consecutive failures, the task stops firing until operator ack
4. **An approval gate** — fires land in `awaiting_approval` until an operator clicks Approve
5. **A run window** — only fire during certain hours of the day
6. **A cost ceiling** — budgets checked before the run, defers if exceeded
7. **Chained successors** — `on_success` and `on_failure` fire another task

Each layer is optional and composes with the others.

---

## Recipe: event-triggered file watcher

Watch a directory for new files, fire an agent when one appears:

```yaml
apiVersion: spark.veilfire.dev/v1alpha1
kind: Task
metadata:
  name: inbox-processor
spec:
  agent: inbox-watcher
  mode: event
  on:
    type: file_changed
    path: ~/Documents/inbox
    recursive: true
    debounce_seconds: 5
  objective: >
    Process new files added to the inbox directory and move them to the
    appropriate destination based on their type.
```

When a file arrives, the `watchdog` observer queues the event. After `debounce_seconds` of quiet, the trigger fires once with up to 50 changed paths as payload. Rapid bursts collapse to a single fire.

---

## Recipe: HTTP polling for new items

Poll a JSON feed for new entries, fire the agent per new entry:

```yaml
spec:
  agent: rss-reader
  mode: event
  on:
    type: http_new_row
    url: https://api.example.com/feed
    allow_hosts:
      - api.example.com
    poll_seconds: 300
    key_path: id
```

The poller fetches the URL every `poll_seconds`, parses the JSON body as a list, and dedupes by `id`. New rows (vs. the last seen set) fire the agent. First poll establishes baseline — only subsequent new rows trigger.

Same SSRF defense as the `http_client` plugin. `allow_hosts` is enforced at the poller, the URL is IDN-normalized, the resolved IP is pinned, and metadata/private IPs are refused.

---

## Recipe: chained tasks

Run a digest generator on Mondays; if it succeeds, fire the announcer; if it fails, fire the alert.

```yaml
# weekly-digest.yaml
spec:
  agent: research-assistant
  mode: recurring
  schedule: { type: cron, expression: "0 8 * * 1", timezone: America/Vancouver }
  objective: "Generate the weekly research digest."
  on_success: weekly-digest-announcer
  on_failure: weekly-digest-alert

# weekly-digest-announcer.yaml — separate task, posts to Slack on success
# weekly-digest-alert.yaml — separate task, sends an alert on failure
```

Depth cap is 5 — you can chain up to five tasks before the runtime refuses further links. The chain is also cycle-detected (A→B→A is refused). The lineage is recorded in `TaskRunRow.triggered_by` as a pipe-delimited string (`task:A|task:B`) so the chain survives process restarts.

---

## Recipe: approval gate for expensive tasks

For a task that costs real money, require manual approval on every fire:

```yaml
spec:
  approval:
    required: true
    note: "Must be reviewed before any spending."
```

The scheduler still triggers it on schedule, but the task lands in `paused` state and an operator has to click **Approve** on the Scheduler page to release it. Approvals are audited.

---

## Recipe: DLQ + retry

A flaky upstream that sometimes times out:

```yaml
spec:
  retry:
    max_attempts: 3
    backoff_seconds: 60.0
    backoff_multiplier: 2.0
    jitter_seconds: 10.0
```

The first retry waits ~70s (60 + jitter), the second ~130s, the third ~250s. After three consecutive failures, the task moves to the **dead-letter queue** (`dlq` state) and stops firing until you click **Ack DLQ** on the Scheduler page. The DLQ transition is audited.

---

## Recipe: run only at night

```yaml
spec:
  only_between: "22:00-06:00 America/Vancouver"
```

The format is `HH:MM-HH:MM TZ`. Windows that cross midnight are supported — the predicate handles the wrap correctly. Fires outside the window raise `PermissionDenied` at `Lifecycle.run_once` and are audited.

---

## Recipe: perpetual with heartbeats

A watchdog that wakes every 5 minutes, checks something, and goes back to sleep:

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

The task emits a heartbeat event every `heartbeat_seconds`. The scheduler's liveness watchdog kills the task (flips it to `failed`) if it skips more than 2× the interval without a heartbeat. Useful for catching silently-wedged perpetual tasks.

**Why `start_at` is required for perpetual:** the contract is "explicit kickoff, no end." A past timestamp (like `2026-01-01`) means "fire on the next cron tick from now." A future timestamp delays the first run.

---

## Recipe: finite recurring window

Run the same cron during a fixed campaign — say, a weekly digest that should only fire from June through August:

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

APScheduler honors both endpoints — fires nothing before `start_at`, removes the job after `end_at`. The trigger persists across server restarts (it's in the SQLite jobstore), so the window survives reboots.

If you want an unbounded recurring task, use `mode: perpetual` (and drop `end_at`). The validator refuses `mode: recurring` without an explicit window.

---

## Recipe: webhook trigger

Let an external system (GitHub, Slack, a CI pipeline, an upstream scheduler) fire an EmberSpark task. Triggers support two auth modes:

- **`bearer`** — caller sends `X-Spark-Token: <token>`. The token is bcrypt-hashed on disk. Good for hand-rolled scripts and internal automation.
- **`hmac_sha256`** — caller sends a body and a signature header (`X-Hub-Signature-256: sha256=<hex>` for GitHub, `X-Slack-Signature` for Slack, or `X-Spark-Signature-256` for generic). The shared secret lives in the age vault. **Required for GitHub, Slack, and most modern providers** because they verify with HMAC, not bearer tokens.

Triggers can also:

- **Forward the request body** to the task as `trigger_payload`. The planner sees a JSON-fenced copy in its first system prompt; the unabridged body is persisted on the run row for replay.
- **Filter inbound events** — a JSON object of dotted-path → expected-value rules. The trigger fires the task only when every rule matches. Empty filter = always fire.
- **Chain via `on_success` / `on_failure`** — when a task completes (or fails), the matching successor task fires automatically. Cycle-detected; depth capped at 5.

### 1. Create the trigger (admin only)

From the Scheduler page (**+ New trigger**), or via API:

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

Response (shown **exactly once**):

```json
{
  "trigger_id": "github-pr-merge",
  "task_name": "code-review-on-merge",
  "auth_mode": "hmac_sha256",
  "secret": "yn2K_FEsk3AlN..."
}
```

**Save this secret immediately.** It's stored in the age vault for HMAC verification but never re-displayed. If lost, delete the trigger and create a new one.

### 2. Fire from the external system

**Bearer mode:**

```bash
curl -X POST \
  -H "X-Spark-Token: yn2K_FEsk3AlN..." \
  -H "Content-Type: application/json" \
  -d '{"hello": "world"}' \
  http://127.0.0.1:7777/api/scheduler/webhooks/github-pr-merge
```

**HMAC mode (GitHub):** GitHub computes `HMAC-SHA256(secret, body)` and sends it in `X-Hub-Signature-256`. EmberSpark verifies in constant time:

```bash
BODY='{"action":"closed","pull_request":{"merged":true,"number":42}}'
SIG=$(printf '%s' "$BODY" | openssl dgst -sha256 -hmac "yn2K_FEsk3AlN..." | awk '{print $2}')
curl -X POST \
  -H "Content-Type: application/json" \
  -H "X-Hub-Signature-256: sha256=$SIG" \
  -d "$BODY" \
  http://127.0.0.1:7777/api/scheduler/webhooks/github-pr-merge
```

On success the task fires and the response is `{"status": "scheduled", "task_name": "..."}`. If the event filter rejects the body the response is `{"status": "filtered"}` (200, no fire). Rate-limit trips return 429. Bad signature returns 401. Vault locked returns 503.

After **10 consecutive bad signatures** the trigger locks for 15 minutes — defends against credential-stuffing on a leaked endpoint URL. The lock surfaces as a `locked` chip in the UI; auditors see a `trigger.locked` audit entry.

### 3. Manage triggers

- List: `GET /api/scheduler/triggers`
- Delete: `DELETE /api/scheduler/triggers/{id}` (audited; for HMAC triggers also deletes the vault secret)

### Slack integration

Slack's Events API uses HMAC-SHA256 with `X-Slack-Signature` (same scheme as GitHub, different header name). Create a trigger with `auth_mode: hmac_sha256`, paste the cleartext into Slack as the **Signing Secret**, and set the Event Subscription URL to `https://your-host/api/scheduler/webhooks/<trigger_id>`. Use an event filter like `{"event.type": "app_mention"}` to fire only on @-mentions of your bot.

---

## Recipe: Telegram bot trigger

Run an EmberSpark task per Telegram message in a whitelisted chat. Uses long-poll `getUpdates`, no public webhook URL required.

```yaml
spec:
  agent: chat-responder
  mode: event
  on:
    type: telegram_message
    bot_token_secret: telegram_bot_token   # name of the secret in the age vault
    allow_chat_ids: [123456789, -987654321] # whitelist of chats / groups
    poll_seconds: 10
    long_poll_timeout: 25
  objective: "Reply to incoming Telegram messages from approved chats."
```

Set the bot token first:

```bash
spark secrets set telegram_bot_token   # paste the token from @BotFather
```

The poller calls Telegram's `getUpdates` with `long_poll_timeout` (default 25s) so the connection stays open until either a message arrives or the timeout elapses. Each new message in a whitelisted chat fires the task once with the message body as `trigger_payload`. Empty `allow_chat_ids` means **any** chat can fire the task — leave it set in production unless you have a reason not to.


---

## Schedule simulation

Before committing a cron expression, test it:

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

Returns the list of predicted fire times over the next 14 days. Nothing is persisted — it's a pure calculation. Useful for checking unusual `*/n` patterns and DST boundaries.

---

## Task state machine

```
created → scheduled ⇄ running → completed / failed
                ↓       ↓
              paused  dlq  (after retry.max_attempts consecutive failures)
                ↓       ↓
              scheduled   scheduled  (after operator ack)
```

States and when you see them:

- `created` — just registered, never fired
- `scheduled` — on the APScheduler job store, waiting for the next tick
- `running` — currently executing
- `paused` — approval gate or manual pause
- `completed` — last run succeeded
- `failed` — last run failed
- `dlq` — hit retry ceiling, won't fire until acked
- `stopped` — manually stopped
- `sleeping` — perpetual task between wakes

Invalid state transitions raise `InvalidTransition` and are refused.

---

## Further reading

- [docs/scheduling.md](https://github.com/Veilfire/EmberSpark/blob/main/docs/scheduling.md) — full reference
- [Cost And Budgets](Cost-And-Budgets) — how budget ceilings gate fires
- [Web UI Guide](Web-UI-Guide) — where the scheduler UI lives
- [Concepts: Permissions](Concepts-Permissions) — how scheduling composes with the permission gates
