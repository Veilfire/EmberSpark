# Web UI Guide

The web UI is the main operator surface. This page walks through the UI's pages and workflows. For per-endpoint API reference, see [API Reference](API-Reference). For source-level details, see [docs/web-ui.md](https://github.com/Veilfire/EmberSpark/blob/main/docs/web-ui.md).

---

## First-time setup

The UI is **disabled by default**. You have to explicitly opt in.

1. `spark config init` to write `~/.spark/spark.yaml`
2. Edit the file; set `spec.web.enabled: true` and pick a bind mode
3. `spark serve`
4. Save the credentials printed to stderr (you'll only see them once)
5. Open the printed URL in your browser
6. Sign in with the username and password

If you lose the credentials, run `spark serve --rotate-credentials` and a new pair is printed.

---

## The sidebar

```
Overview           (bell)
Chat
Runs
Persona
Plugins
Scheduler
Cost & Budgets
Memory
Downloads
Skills
Stats
Security Center
Guardrails
Audit Log
Ops
Settings
```

The **bell** in the top bar aggregates every HITL signal into one surface
— pending skill reviews, paused tasks awaiting approval, DLQ'd tasks,
expiring internal-IP grants, "raw logging left on" warnings, cost-budget
trips, critical incidents, and new files written to the data volume's
deliverables directory. Click a row to navigate to the target page with
the pending item focused. Per-kind toggles live on the **Settings** page.

The order reflects what you'll look at most often. Use `cmd+K` (macOS) / `ctrl+K` (Linux) to open the command palette and fuzzy-search instead of clicking. See [Command Palette](Command-Palette) for the full shortcut list.

---

## Page tour

### Overview

The landing page. Shows:

- **Spend (24h)** — cost across all runs (task + chat) in the last day, with the per-hour sparkline
- **Spend (all-time)** — cumulative cost across every recorded run since the database was initialized; subtitle shows the model + agent count contributing
- **Active runs** — how many tasks are running right now
- **Agents** — how many agent YAMLs are installed
- **Posture** — frozen / standard / audit, plus the default privacy mode
- **Recent runs** — last 10 runs with state, task, duration, iterations, tool calls

If EmberSpark is frozen, the Overview has a red banner. The Incident Banner also persists at the top of every page when there's a recent `critical`-severity audit entry.

### Chat

Conversational session-based UI. Pick an agent, click **Start session**, type a message.

- WebSocket streaming for live response
- Session memory persists across messages (up to 500 entries, FIFO eviction)
- Long-term memory (when enabled for the agent) is retrieved per turn and injected as "Known context" in the system prompt
- A **capability preamble** is prepended to the system prompt so the model knows it has persistent memory, session memory, and (if enabled) cross-agent shared memory — without this, models trained as stateless chatbots confidently deny the capability
- **Plugin tools** — when the agent's `plugins.allow` lists any plugins, they're bound to the chat model the same way they are for task runs. Tool calls fire real plugins through the sandbox; the chat surface shows a `→ plugin(args)` line when the agent invokes a tool and a `← plugin: result` line when it returns (or `✗` on error). Default chat-turn caps: 8 tool calls / 12 model rounds / 12 iterations, reset every user turn. Empty allowlist → pure-text chat (no tools).
- **Operator-config visibility** — the same per-plugin `allow_paths` / `allow_hosts` / `rules` you saved in the Plugins page are rendered into the chat's system prompt under "Operator config (effective for this run)". The agent picks valid arguments on the first call instead of guessing.
- **Cost telemetry** — every chat turn writes a `model_call_events` row plus a `cost_events` aggregate keyed by a synthetic `chat-{turn_id}-…` run id, so chat spend lands in the Cost dashboard alongside task runs. OpenRouter rows go through the same deferred-enrichment flip from `computed` to `reported` USD.
- **Citations footer** under each assistant message surfaces the memory ids that were retrieved for that turn; click a citation to open the full memory
- Per-session chat settings: adjust history window, turn LTM retrieval on/off, toggle global memory inclusion, pin / exclude specific memory ids
- Every message uses the current active persona
- Mobile-responsive — the chat page is the one page designed to work on a phone

See [Persona Manager Guide](Persona-Manager-Guide) for the edit-persona-mid-chat workflow.

### Runs

Filterable list of every task run — complete, failed, running, paused, sleeping, dlq. Filters:

- **State** — dropdown
- **Task name** — substring match

Click a run_id to go to the **Run Replay** page.

### Run Replay

The replay page renders the run's **outcome** alongside its execution trace.

- **Final response** — the planner's last assistant message, rendered as markdown. This is the answer the agent produced when it stopped calling tools (or the structured error if it failed). For tasks with `output: { type: file }` configured, the same content is also written to a markdown file under `<deliverables>/<task_name>/<run_id>.md`.
- **Reflection summary** — the post-run reflector's compact digest (only present when the agent has `runtime.reflection: true` and the run completed).
- **Deliverables** — a sidebar listing every file the run produced, with size, kind, and a one-click download link. Clicking opens the file via `/api/deliverables/...`. Each row appears on the Downloads page too, cross-linked back to this run.
- **Trigger payload** (when present) — collapsible JSON of the inbound webhook / external trigger body that fired this run. The planner saw a (truncated to 32 KB) copy in its first system prompt; the panel here shows the full unabridged copy persisted on the run row.
- **Cost chip** — total run cost in the header next to iter / model-call / tool-call counts. The hover tooltip shows the source mix (`N reported · M computed`).
- **Model calls** — a per-iteration table of every model invocation: model id, input/output/cache token counts, latency in ms, dollar cost, a `✓` (provider-authoritative) or `≈` (locally computed from the price table) badge, and the request id. For OpenRouter calls the request id is a deep-link to the corresponding entry on the OpenRouter activity dashboard. Reasoning tokens (o-series, extended thinking) are flagged inline next to the output count.
- **Flame graph** — every span as a bar whose width is its duration and whose position is its offset from run start. Parent spans wrap their children. Error spans are red.
- **Timeline table** — depth, span name, duration (ms), error class.

Use this page when you're judging *what the agent produced*, debugging *why a run was slow*, or auditing *which tool call failed*.

### Persona

Edit the agent's system prompt live. Left pane: list of personas with the active one tagged. Right pane: editor with monospaced textarea for the system prompt, inputs for name / tone / description / tags. **Preview** button renders the assembled system message. **Save & Activate** writes an elevated audit entry and the next model call picks up the change.

See [Persona Manager Guide](Persona-Manager-Guide).

### Plugins

The operator config for every registered plugin. Left pane: list of plugins with version. Right pane: dynamic form rendered from the plugin's `config_schema`. Each field is typed (string / number / bool / enum / list).

To edit:

1. Pick a plugin
2. Fill in the form
3. Type a reason
4. Click Save

Changes take effect on the next tool call. See [Using Plugins](Using-Plugins) and the per-plugin references.

### Scheduler

Four sections:

- **Agents** — every registered agent
- **Tasks** — every task, with **Edit** / **Trigger now** / **Pause** / **Stop** buttons. **+ New task** button at the top of the section opens the task creator.
- **Schedules** — every task that has a schedule, with trigger expression, timezone, and a "next 5 fires" preview.
- **Triggers** — every webhook integration, with auth mode chip (`bearer` / `hmac_sha256`), payload-forwarding flag, event filter flag, fire counters, and lock state. **+ New trigger** opens a modal that walks you through:
  1. Trigger ID + target task.
  2. Auth mode (bearer vs HMAC-SHA256).
  3. Payload forwarding (whether the inbound body lands on `RunState.trigger_payload`).
  4. Optional **event filter** — a JSON object of dotted-path → expected-value pairs (e.g. `{"action": "closed", "pull_request.merged": true}` for "fire only on merged GitHub PRs").
  5. Hourly rate limit.
  
  On create, the cleartext credential is shown **exactly once**. Save it immediately. HMAC-mode triggers also store the secret in the age vault under `webhook.trigger.<id>.hmac_secret`.

Plus the F5 additions (approval queue, DLQ list) — see [Scheduling Guide](Scheduling-Guide).

#### Task creator + editor

A single modal handles both create and edit. Fields:

- **Name** — slug; locked when editing (renames not supported).
- **Agent** — dropdown of registered agents. Changing the agent on an existing task is allowed but audited at `elevated` severity (it rebinds plugins, permissions, and memory namespace).
- **Mode** — `one_shot` / `recurring` / `perpetual`. The schedule block below is mode-gated:
  - `one_shot` — schedule optional. "Schedule for later" toggle exposes a `start_at` input only.
  - `recurring` — schedule + `start_at` + `end_at` all required. Both timestamps must be provided and `start_at < end_at`.
  - `perpetual` — schedule + `start_at` required; the `end_at` field is hidden.
- **Objective** — textarea, required.
- **Inputs** — key/value pairs.
- **Schedule** — pick `cron` (visual builder, see below) or `interval` (raw seconds).
- **Budgets** — collapsible. Override `max_runtime_seconds`, `max_model_calls`, `max_tool_calls`, `max_tokens_per_run`. Empty fields inherit from the agent.
- **Forensic** — collapsible. Default off; enabling requires a non-empty reason.
- **Auto-start** — checkbox shown only for create (not edit). When checked + a schedule is present, the task is registered with the scheduler immediately.

Edits while a run is in flight are refused with a 409. Re-scheduling on edit is automatic.

#### Visual cron builder

The cron schedule field is a preset picker, not a raw text input. Eight presets:

- **Every N minutes** / **every N hours** — `*/N * * * *` / `0 */N * * *`
- **Daily at TIME** — `M H * * *`
- **Every weekday at TIME** — `M H * * mon-fri`
- **On selected weekdays at TIME** — `M H * * <days>` with weekday checkboxes
- **Monthly on day at TIME** — `M H D * *`
- **Yearly on month/day at TIME** — `M H D MO *`
- **Custom cron expression** — escape hatch for anything outside the presets

The generated cron string is always shown in monospace below the inputs so power users see exactly what's emitted. Edits round-trip cleanly: loading a saved task with `0 8 * * mon,wed,fri` lands on "On selected weekdays at 08:00" with Mon/Wed/Fri pre-checked. Anything that doesn't match a preset (e.g. `0 9-17 * * 1-5`) lands in the **Custom** preset with the original expression preserved.

Live preview ("Next 5 fires") uses the existing `/api/scheduler/simulate` endpoint and updates as the operator changes preset fields.

### Cost & Budgets

- **Period toggle** — day / week / month
- **Breakdowns** — by provider, by agent, by model
- **Budget CRUD** — create, delete, list budgets with scope (global / agent / provider), period (daily / weekly / monthly), limit, soft alert, hard stop
- **Recent cost events** — per-run spend log

Budgets with `hard_stop: true` refuse runs that would exceed the ceiling for the current period. See [Cost And Budgets](Cost-And-Budgets).

### Memory

Two views:

- **Long-term index** — browse records in the Chroma store, filter by namespace, see summaries + type + sensitivity + retention class. Click delete on a row to remove it from Chroma (audited).
- **Playbook stats** — per-agent playbook library with success rate (Beta α/β), uses, avg duration, avg tool calls, tool sequence. This is the learning system's strategic layer.

See [Memory Browser](Memory-Browser) and [Concepts: Memory](Concepts-Memory).

### Downloads

Lists every file under the data volume's `deliverables` subdirectory. When a plugin writes a new file here (e.g. `image_gen` saving a generated image, `markdown_writer` saving an agent's report, or any custom plugin), the file appears in this list and the **notification bell** fires a `download_ready` event.

Each row has two actions:

- **Preview** (eye icon) — only shown for `.md` / `.markdown` files. Opens an inline modal that fetches the file and renders it through the same `MarkdownView` component the chat and replay pages use. Click outside the modal or hit `Esc` to close. The download button stays in the modal header for one-click save.
- **Download** — streams the raw bytes via `Content-Disposition: attachment`. Works for every file type.

The directory is operator-configured in `SparkRuntime`'s `data_volume` block. If no data volume is set, this page returns a 404 with a configuration hint.

### Settings

Three sections:

- **Notification categories** — every notification kind (download ready, pending skill review, approval required, DLQ, expiring IP grant, raw-logging-on, cost alerts, incidents, plugin hash drift, memory pruned) has its own toggle. Turning a category off means no row is written and no bell/toast fires for that kind — the underlying event still happens, you just don't get nagged.
- **Security — session timeout** — admin-only control over how long a signed-in session stays valid. Toggle the timeout on/off; when on, dial it with days / hours / minutes inputs (1 minute minimum, 30 days maximum). When off, the inputs grey out and sessions don't expire. Saves take effect immediately for new and existing sessions — no restart required. Every change is audited at `elevated` severity.
- **Delivery** — toast-on-create + sound-on-elevated toggles.

Notification preference changes are audited at `info` severity.

### Skills

- **Pending reviews** — each pending skill shows the AI-suggested name and description (editable), the service, the base URL, the required hosts/secrets, the confidence, and a link to the source doc. Approve or Reject with notes.
- **Approved skills** — per-agent list of active skills.

See [Skill Catalog](Skill-Catalog) and [Concepts: Skills](Concepts-Skills).

### Stats

Rolling 7-day agent metrics:

- Runs total / completed / failed
- Success rate
- Wall time p50 / p95
- Total cost / avg cost per run
- Memory writes / skill approvals

One agent (EmberSpark is single-agent). Refreshes on page load.

### Security Center

The 8-tab policy editor. See the dedicated [Security Center Guide](Security-Center-Guide).

### Guardrails

Last 24h aggregation of critical / elevated / info audit events, broken out by category (permission denied, sandbox denied, budget exceeded, plugin hash changed, internal grants, raw logging, skill rejected/approved). Clickable into the audit log with pre-filtered queries.

Use this as your daily smoke test — if anything unexpected is in the critical count, investigate.

### Audit Log

Every security-relevant mutation, with filters:

- **Kind** — substring match
- **Severity** — info / elevated / critical

Each row shows the timestamp, actor (operator subject), kind, target, severity chip, and the diff or reason.

The audit log is **immutable** — there's no UI to delete entries. If you need to prune old entries (for disk reasons, not for cover-ups), do it from sqlite directly, and only for entries older than your retention requirement.

### Ops

Host diagnostics:

- **Sandbox status** — backend name + "ok" or the failure reason
- **Disk usage** — free / total on the EmberSpark state directory's volume
- **Data residency** — where the DB, Chroma store, logs, scheduler store, and web token live; sizes on disk
- **Plugin registry** — every loaded plugin with version and module hash. The list is seeded at startup from `default_registry()` (so all 18 built-ins appear immediately on a fresh install) and refreshed on every plugin invocation; a `module_hash` change between boots flags code drift.
- **Live log tail** — SSE stream from the JSONL log at `~/.spark/logs/spark.jsonl`. Backfills the **last 50 lines on connect** so the panel populates immediately, then continues `tail -f` style with new lines as they're written. Connect is via `/api/stream/logs`; data lines are unnamed SSE events (`data:` only) so the standard `EventSource.onmessage` handler picks them up.

Use this when you need to verify something about the host without dropping to a shell.

---

## The command palette

`cmd+K` (macOS) or `ctrl+K` (Linux) opens an overlay with a fuzzy search input. Type to filter by page name or description. `Enter` to navigate, `esc` to close.

Works from any page. Useful for:

- Jumping to a page when the sidebar is off-screen (mobile)
- Not having to remember which sidebar slot a feature is in
- Faster than clicking

---

## Responsive behavior

The UI is **desktop-first but not desktop-only**. The Chat page specifically is designed to work on mobile (portrait phone at 375px). Other pages collapse the sidebar to a horizontal scroll at narrow widths but prioritize data density over mobile polish.

If you're on a phone and want to chat with the agent from anywhere on your LAN:

1. Set `bind.mode: lan` in `~/.spark/spark.yaml`
2. Set `bind.allowed_cidrs` to your home network CIDR
3. `spark serve`
4. Open `http://<laptop-ip>:7777` on your phone
5. Sign in
6. Go to Chat

Everything works — the WebSocket chat streams, the persona preview renders, the security center tables are scrollable.

---

## Keyboard shortcuts

| Key | Action |
|---|---|
| `cmd+K` / `ctrl+K` | Open command palette (works from any input) |
| `?` | Show keyboard shortcuts help overlay |
| `/` | Focus the page's search input |
| `esc` | Close the open modal / palette |

`cmd/ctrl+K` is the only entry point for navigation. See [Command Palette](Command-Palette) for details.

---

## Auth and sessions

The UI login form asks for the username and password generated at startup. Successful login issues a session cookie with:

- `HttpOnly`
- `SameSite=Strict`
- `Secure` when bind mode is `public` or `SPARK_WEB_COOKIE_SECURE=1`
- TTL driven by the **Settings → Security** panel (default **1 hour**, admin-configurable, toggle-off to disable entirely)

When the session timeout toggle is off, the signed cookie's age check is skipped and the browser cookie gets a 10-year Max-Age so it survives browser restarts. The operator controls this from the UI without a server restart — changes to the TTL take effect on the next request.

Every state-mutating route checks the cookie (or the `X-Spark-Token` header for headless clients). The token grants **admin** role; the session cookie grants **admin** role on login.

Log out from the sidebar footer at any time. Sessions are not server-side — logout just deletes the cookie in the browser.

### Time display

Timestamps throughout the UI render in two styles:

- **Relative** ("2m ago", "3d ago") for freshness cues. Components tick every 30 seconds so the reading doesn't go stale.
- **Absolute** ("2026-04-17 14:32") for agent/run timestamps where an accurate reading matters more than a rough sense of recency.
- **Countdown** ("in 5d", "in 2h") for expiry fields like grant/forensic `expires_at`, so a future date reads correctly.

All timestamps are normalized to UTC at the storage boundary and rendered in the operator's local timezone.

---

## Further reading

- [docs/web-ui.md](https://github.com/Veilfire/EmberSpark/blob/main/docs/web-ui.md) — source-level reference
- [Security Center Guide](Security-Center-Guide) — the policy editor
- [Command Palette](Command-Palette) — keyboard shortcuts deep dive
- [API Reference](API-Reference) — every HTTP endpoint
