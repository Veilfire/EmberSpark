# Using Plugins — Operator Guide

This is the operator's guide to the built-in EmberSpark plugins. It walks through the mental model, the five-gate permission model, and the day-to-day workflow. For per-plugin field references, see the Plugin Reference pages in the sidebar. For the theoretical backdrop, see [Concepts: Plugins](Concepts-Plugins) and [Permissions Guide](Permissions-Guide).

---

## The built-ins at a glance

**Core filesystem + process + network:**

| Plugin | Purpose | Default state |
|---|---|---|
| **filesystem** | Bounded read / write / list / stat against an allowlist | **Works on the data volume** — empty `allow_paths` falls back to `ctx.scratch_path` + `ctx.deliverables_path` |
| **http_client** | SSRF-hardened outbound HTTPS (flat allowlist) | Inert — empty `allow_hosts`. (`http_tool` is the open-browse alternative.) |
| **http_tool** | Per-host method matrix + readability | **Works out of the box** — default `rules` is one `*` GET-only rule with a 5 MB cap and main-content extraction on |
| **markdown_writer** | `.md` / `.markdown` file writes against an allowlist | **Works on the data volume** — empty `allow_paths` falls back to `ctx.deliverables_path` |
| **shell** | Argv-only command execution with a per-command flag allowlist | **Disabled + empty allowlist** |
| **sqlite** | SQL queries against operator-allowlisted databases with a sqlglot gate | Inert — empty `databases` |

**Research & structured data:**

| Plugin | Purpose | Default state |
|---|---|---|
| **web_search** | Provider-agnostic web search (Brave / Serper / Tavily / DDG / Bing) | **Works out of the box** — provider defaults to `ddg_html` (no API key needed); switch to a paid provider for production |
| **pdf_reader** | PDF text + metadata extraction | **Works on the data volume** — empty `allow_paths` falls back to scratch + deliverables |
| **csv_io** | Read/write CSV files | **Works on the data volume** — empty `allow_paths` falls back to scratch + deliverables |
| **json_query** | JMESPath filter over JSON payloads | **Works out of the box** |
| **rss_reader** | Fetch + parse RSS/Atom feeds | Inert — empty `allow_hosts` |
| **datetime** | Time utilities (no secrets, no network, no filesystem) | **Works out of the box** |
| **propose_skill** | Agent-side skill proposal — formally queues a `behavior` / `knowledge` / `api` skill for human review on the Skills page | **Works out of the box**; rate-limited per agent. Add to allowlist for any agent you want to be able to self-improve. See [Plugin Reference: propose_skill](Plugin-Reference-Propose-Skill). |

**Side effects:**

| Plugin | Purpose | Default state |
|---|---|---|
| **email_sender** | SMTP send-only with operator-locked sender + domain allowlist | Inert — no SMTP host configured |
| **git** | Narrow git operations with structured output | Inert — empty `allow_repos` |
| **image_gen** | Provider-agnostic image generation → deliverables directory | Inert — no API key; requires data volume |

**External integrations:**

| Plugin | Purpose | Default state |
|---|---|---|
| **webhook** | Outbound HMAC-signed POST to allowlisted hosts (Slack incoming hooks, Zapier, n8n, …) | Inert — empty `allow_hosts` |
| **telegram_messenger** | Telegram Bot API (send / edit / inline keyboards / commands), pairs with the long-poll bot runner | Inert — empty `allow_chat_ids`; needs `telegram_bot_token` in vault |
| **home_assistant** | View states + (opt-in) call services on Home Assistant. HA's HomeKit Controller integration bridges Apple HomeKit through the same surface. Custom Plugins-page editor with live-discovery checkbox grids; danger domains gated behind typed-confirm. See [Plugin Reference: home_assistant](Plugin-Reference-Home-Assistant). | Read-only; safe domains (light / switch / sensor / media_player / climate / scene / …) pre-checked; danger domains (lock / camera / device_tracker / person / alarm_control_panel / vacuum) excluded; needs `home_assistant_token` in vault + internal-IP grant |

Most plugins ship **inert**. That's deliberate. You opt in explicitly by populating its config in the Plugins page of the web UI. The exceptions — `json_query` and `datetime` — have no network, no filesystem, no secrets, and no reason to be inert.

---

## The three-step workflow

For every plugin you want an agent to use, walk these three steps:

### Step 1 — Allow the plugin in the agent YAML

```yaml
spec:
  plugins:
    allow:
      - http_client
      - markdown_writer
```

This is the coarsest gate. If you don't list it here, no amount of config or grants will make the plugin callable by this agent.

### Step 2 — Grant the plugin's required permissions

Every plugin declares what it needs. The agent has to grant at least that set:

```yaml
spec:
  permissions:
    grants:
      - net.http        # http_client needs this
      - secrets.read    # http_client also needs this
      - fs.write        # markdown_writer needs this
```

Missing a grant = plugin refused at call time = audit entry, sanitized error to the model.

### Step 3 — Configure the plugin in the web UI

Open **Plugins** in the sidebar. Pick the plugin. Fill in the form. Type a reason. Save.

This is where the real narrowing happens. The agent YAML granted the *capability*; the plugin config sets the *scope*. Operator values override model values on any overlapping field.

---

## Why the three steps are separate

Each step answers a different question:

- **Allowlist** — "is this capability on the menu for this agent?"
- **Grants** — "does the agent have the capability at the abstract level?"
- **Config** — "what exactly can it touch?"

You can have a plugin that is allowed, granted, and still useless because its config is empty. That's the intended first-run state — you see the plugin in the Plugins page, you have to explicitly configure it before anything happens.

This is opposite to the usual "install → works" model. In EmberSpark, **install → inert → configured → works**. It's one more step and it's intentional.

> **The model sees the config you saved.** When the agent runs (task or chat), the runtime renders an "Operator config" block per plugin into the system prompt with the effective `allow_paths`, `allow_hosts`, `rules`, `allowed_methods`, `enabled` toggles, etc. You don't need to repeat your allowlist in the agent YAML or in the user's instruction — the model picks values inside the constraints because it can read them. If you tighten a path or shrink a host list, the next call sees the new value (no restart).

---

## Plugins in chat sessions

Chat agents have the same plugin reach as scheduled tasks. The agent's `plugins.allow` list is wired to a real `ToolExecutor` (sandboxed, rate-limited, audited) and tool calls fire real plugins — `markdown_writer` writes a real file, the deliverables watcher catches it, the Downloads page updates, a notification fires. Use the chat surface for interactive workflows and the scheduler for unattended ones.

Chat-side budget caps are tighter than task runs (default 8 tool calls / 12 model rounds / 12 iterations) since chat is interactive and an out-of-control loop is visible. The cap is per chat *turn*, not per session — every new user message resets it.

If your agent's `plugins.allow` is empty, chat falls back to the pure-text path. If it has plugins listed but you'd rather chat be conversation-only, point the chat at a different agent without that allowlist (you can have multiple agent YAMLs).

---

## A typical operator workflow

Let's say you want an agent that fetches GitHub API data and writes markdown notes. Here's the sequence:

1. **Create an agent YAML** listing `http_client` and `markdown_writer` in `plugins.allow`, with grants for `net.http`, `secrets.read`, `fs.write`, and `allow_paths: [~/workspace]`, `allow_hosts: [api.github.com]`.
2. **Store your GitHub PAT** in the age vault: `spark secrets set github_pat`.
3. **Validate the agent**: `spark agent validate ~/.spark/agents/gh-researcher.yaml`.
4. **Start the web UI**: `spark serve` — save the credentials printed on startup.
5. **Open the Plugins page** in the UI.
6. **Configure http_client**:
   - `allow_hosts`: `api.github.com`
   - `allow_http`: off
   - `allowed_methods`: `GET`
   - `user_agent`: something descriptive
   - Reason: "initial config for gh-researcher"
   - Save.
7. **Configure markdown_writer**:
   - `allow_paths`: `~/workspace/notes`
   - `allow_overwrite`: off
   - `allow_append`: on
   - Reason: "initial config"
   - Save.
8. **Write a task YAML** with an objective that says what to do.
9. **Run it**: `spark task run task.yaml --agent agent.yaml`.
10. **Open Runs** in the UI, click the run id, see the flame graph.

That's it. From here you iterate — edit the persona, tighten the plugin config, widen if you need more capability, narrow if the agent is reaching for things you don't want.

---

## Iterating safely

The best property of EmberSpark's plugin system is that you can tighten any plugin's config without restarting anything. Every config write takes effect on the next tool call.

Suppose you realize the agent is reading larger responses than you'd like. You don't kill the task. You:

1. Open **Plugins** → `http_client`.
2. Change `max_response_bytes` from `5000000` to `500000`.
3. Reason: "response size concern".
4. Save.

The next HTTP call inside any running task hits the new limit. The old ones already finished. No restart, no migration.

Same with `shell.allowed_commands` — you can add or remove commands live. The next invocation uses the new list.

---

## Reading the audit trail

Every plugin config change is an `elevated`-severity entry in the audit log. Open the **Audit Log** page and filter by `kind=plugin.config.update` to see the full history:

- Who changed what
- When
- The diff (what fields changed)
- The reason the operator typed

This is the paper trail you'll want when asking "when did I tighten that allow list?" or "did I really approve this config change last week?"

---

## Common mistakes and how EmberSpark catches them

### "The agent keeps failing with 'plugin requires permissions: subprocess'"

You added `shell` to `plugins.allow` but forgot to grant `subprocess` in `permissions.grants`. Add the grant, re-run.

### "The agent says 'host not in the allowlist' even though I set it"

The agent YAML's `network.allow_hosts` is now **advisory** — it doesn't gate per-call host validation, only the `net.http` grant gates whether the sandbox shares the network namespace. The actual hostname check happens inside each plugin via `HostPolicy`. So if `http_client` denies a host, set `http_client.allow_hosts` in the **Plugin Config** (or for `http_tool`, add a named-host rule). For a fresh install you usually want `http_tool` instead — it ships with a `*` GET-only fallback rule.

### "The agent wrote a file in the wrong place"

Check the `markdown_writer.allow_paths` in the Plugin Config. With an empty list the plugin now falls back to `ctx.deliverables_path` (the data volume's deliverables root) — set explicit paths if you want the agent's writes pinned somewhere narrower.

### "The agent is burning through tokens"

Check the `runtime.max_model_calls` in the agent YAML. It defaults to 30, which is usually plenty but can be too loose for chat-heavy tasks.

### "I keep getting rate-limit errors from the webhook endpoint"

Each trigger has its own `rate_limit_per_hour`. The default is 60. If you're firing faster, raise it — or slow down the upstream caller.

### "The shell plugin isn't running anything"

Check two places:

1. `shell.enabled` in the Plugin Config — it's `false` by default
2. `shell.allowed_commands` — the map of allowed named commands, empty by default

Both must be non-empty for the plugin to do anything.

### "I got a 'sandbox unavailable' on startup"

You don't have Bubblewrap (Linux) or Seatbelt (macOS). `spark serve` refuses to start without a working backend. See [Installation](Installation).

---

## When to reach for which plugin

Here's a quick decision tree:

- **"I need to read or write files"** — `filesystem` / `markdown_writer` / `csv_io`
- **"I need to produce a markdown report"** — `markdown_writer` (narrower than filesystem)
- **"I need to read a PDF"** — `pdf_reader`
- **"I need to parse a CSV"** — `csv_io`
- **"I need to call a public HTTP API"** — `http_client` (single API) or `http_tool` (multi-host with per-host method allowlists)
- **"I need to search the web"** — `web_search` (Brave / Serper / Tavily / DDG / Bing)
- **"I need to filter a JSON response"** — `json_query` (JMESPath)
- **"I need to know the current date"** — `datetime`
- **"I need to poll an RSS feed during a run"** — `rss_reader` (or the scheduler's `http_new_row` event source for cron-driven polling)
- **"I need to send an email"** — `email_sender` (SMTP)
- **"I need to query git history"** — `git` (structured) or `shell` with git log allowlisted (raw text)
- **"I need to query a SQLite database"** — `sqlite` (read-mode unless you trust the agent with writes)
- **"I need to generate an image"** — `image_gen` (requires the data volume to be enabled)
- **"I need to run a specific shell command"** — `shell` with that exact command in the allowlist
- **"I need to push notifications to Slack / Zapier / a downstream service"** — `webhook` (signed) or pair an [outbound trigger]( Webhook-Provider-Profiles ) with the inbound webhook system for round-trips.
- **"I need a Telegram chatbot interface to my agent"** — `telegram_messenger` for outbound + the bot runner via `mode: event, on: { type: telegram_bot, ... }` for inbound. See [Telegram Bot Setup](Telegram-Bot-Setup).

Don't add more plugins than you need. The safest plugin is the one that isn't there.

---

## Per-plugin references

**Core:**
- [Plugin Reference: filesystem](Plugin-Reference-Filesystem)
- [Plugin Reference: http_client](Plugin-Reference-HTTP-Client)
- [Plugin Reference: http_tool](Plugin-Reference-HTTP-Tool)
- [Plugin Reference: markdown_writer](Plugin-Reference-Markdown-Writer)
- [Plugin Reference: shell](Plugin-Reference-Shell)
- [Plugin Reference: sqlite](Plugin-Reference-SQLite)

**Research & data:**
- [Plugin Reference: web_search](Plugin-Reference-Web-Search)
- [Plugin Reference: pdf_reader](Plugin-Reference-PDF-Reader)
- [Plugin Reference: csv_io](Plugin-Reference-CSV-IO)
- [Plugin Reference: json_query](Plugin-Reference-JSON-Query)
- [Plugin Reference: rss_reader](Plugin-Reference-RSS-Reader)
- [Plugin Reference: datetime](Plugin-Reference-Datetime)

**Side effects:**
- [Plugin Reference: email_sender](Plugin-Reference-Email-Sender)
- [Plugin Reference: git](Plugin-Reference-Git)
- [Plugin Reference: image_gen](Plugin-Reference-Image-Gen)

**External integrations:**
- [Plugin Reference: webhook](Plugin-Reference-Webhook) (outbound HMAC-signed POST)
- [Plugin Reference: telegram_messenger](Plugin-Reference-Telegram-Messenger) (Telegram Bot API)

---

## Further reading

- [Concepts: Plugins](Concepts-Plugins) — the abstract model
- [Permissions Guide](Permissions-Guide) — the five-gate permission walk
- [Plugin Authoring](Plugin-Authoring) — writing your own
- [docs/plugin-config.md](https://github.com/Veilfire/EmberSpark/blob/main/docs/plugin-config.md) — the source-level reference for every config field
