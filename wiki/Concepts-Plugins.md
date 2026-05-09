# Concept: Plugins

Everything the agent does to the outside world — reading a file, calling a URL, running a command, querying a database — goes through a **plugin**. A plugin is the narrow seam between the model and the real system.

---

## The shape of a plugin

Every EmberSpark plugin is a Python class that declares:

| Field | What it is |
|---|---|
| `name` | Unique identifier the model and operator reference. |
| `version` | Semver string. Shows up in the plugin registry. |
| `description` | One-line purpose statement shown in the web UI. |
| `input_schema` | Pydantic model — what the model is allowed to send per call. |
| `output_schema` | Pydantic model — the strict shape of the plugin's return value. |
| `config_schema` | Pydantic model — the operator-editable config. **This is the key new thing.** |
| `required_permissions` | Set of `Permission` enum values the plugin needs to function. |
| `required_secrets` | Set of secret names the plugin wants injected. |
| `sensitivity` | `LOW` / `MODERATE` / `HIGH` / `RESTRICTED` — controls privacy filtering. |
| `filter_output_before_model` | Whether to run outputs through the privacy pipeline before the model sees them (default `True`). |
| `needs_network` | Whether the sandbox child should have a network namespace. |

And an async method:

```python
async def execute(self, args: InputSchema, ctx: ToolContext) -> OutputSchema: ...
```

That's the entire contract. EmberSpark ships **17 built-in plugins** (core + side-effects + external integrations) and you can write your own by following the same shape. See [Plugin Authoring](Plugin-Authoring).

---

## Why two schemas?

EmberSpark's plugin system is deliberately split between **what the model can set** (`input_schema`) and **what the operator controls** (`config_schema`). These can overlap, and when they do, **the operator wins**.

This is the mechanism that lets you narrow an agent through the web UI without touching any YAML:

- The operator sets `http_client.allow_hosts: ["api.github.com"]` in the Plugins page.
- The model, inside its tool call, tries `allow_hosts: ["evil.example"]`.
- At the merge step in `ToolExecutor`, the operator's value replaces the model's value.
- The plugin receives `allow_hosts: ["api.github.com"]` and the SSRF defense blocks any attempt to reach `evil.example`.

Fields that are **operator-only** (present in `config_schema` but not in `input_schema`) are passed via `ctx.plugin_config` instead. These are things like:

- `filesystem.read_only` — a big red switch
- `http_client.allowed_methods` — the set of allowed HTTP verbs
- `shell.enabled` — master switch for the shell plugin
- `markdown_writer.allow_append` — disable append mode

The plugin's `execute` method reads these from the context and enforces them in-code.

> **The model gets to see the resolved config.** At every model invocation, the runtime renders an "Operator config (effective for this run)" block per plugin into the system prompt, listing the constraints the model needs to plan around — `allow_paths`, `allow_hosts`, `rules`, `allowed_methods`, `enabled` toggles, `provider`, `databases`, `allow_repos`, etc. (Noise like `user_agent` and timeouts is filtered out.) That visibility means an agent picks valid argument values on the first call instead of guessing from priors and tripping `PATH_DENIED` on every fresh install.

---

## Plugin lifecycle

A plugin goes through three states for any given agent:

1. **Registered** — the plugin class exists in the EmberSpark registry. Built-ins are registered at startup; entry-point plugins are auto-discovered from installed packages.
2. **Allowed** — the agent's YAML lists the plugin in `spec.plugins.allow`. Without this, the plugin cannot be called for this agent, period. Registration ≠ usability.
3. **Configured** — the operator has entered values in the plugin's config form. The auto-seeded defaults are usually inert (empty allowlists, disabled switches) so the plugin does nothing useful until configured.

A plugin can be registered, allowed, and **inert** because the config hasn't been populated. That's the intended first-run state for `shell` and `sqlite` in particular — they ship disabled.

---

## How plugins are invoked

When the engine receives a tool call from the model:

```json
{"tool": "http_client", "args": {"url": "https://...", "method": "GET", ...}}
```

It routes through [`ToolExecutor.call`](https://github.com/Veilfire/EmberSpark/blob/main/spark/plugins/tool_runtime.py) which walks the five layers:

1. **Allowlist** — is `http_client` in `agent.spec.plugins.allow`?
2. **Permissions** — is `plugin.required_permissions` ⊆ `agent.spec.permissions.grants`?
3. **Budget** — `budget.tick_tool()` — under the ceiling?
4. **Config merge** — load operator config from DB, merge into args, separate operator-only fields into `plugin_config`.
5. **Secret resolution** — resolve only the secrets the plugin declared in `required_secrets`.
6. **Sandbox policy** — build the policy from agent permissions + plugin network need.
7. **Dispatch** — spawn the sandbox worker with the `RequestFrame`, wait for the `ResponseFrame`.
8. **Output validation** — validate against `output_schema`.
9. **Privacy filter** — run the result through `filter_for_model` if `filter_output_before_model=True`.
10. **Log + return** — emit `tool.invoked`, `tool.result_received`, return the filtered content to the engine.

At any step a failure is classified into a category (`permission_denied`, `network_denied`, `path_denied`, `budget_exceeded`, `sandbox_timeout`, etc.) and logged with a sanitized error message for the model — schema field names never leak.

---

## Inside the sandbox

The plugin runs inside a child process. It sees a tiny subset of the filesystem, no environment variables from the parent, and (unless `needs_network=True`) no network namespace. See [Concepts: The Sandbox](Concepts-Sandbox) for the details.

From inside, the plugin receives:

- `args: InputSchema` — the merged args, already validated
- `ctx.secrets: dict[str, str]` — only the secrets declared in `required_secrets`
- `ctx.plugin_config: dict[str, Any]` — the full operator config (including operator-only fields)
- `ctx.privacy_mode: str` — the current privacy mode, informational

The plugin's only way back to the parent is the JSON response frame. No logging, no side channels, no shared memory.

---

## Built-ins

Each has its own reference page with the full config schema, defaults, and operator workflow.

### Core

| Plugin | Reference | What it does |
|---|---|---|
| `filesystem` | [Plugin Reference: filesystem](Plugin-Reference-Filesystem) | Bounded read/write/list/stat against an allowlist |
| `http_client` | [Plugin Reference: http_client](Plugin-Reference-HTTP-Client) | SSRF-hardened outbound HTTPS with host allowlist + method gate |
| `http_tool` | [Plugin Reference: http_tool](Plugin-Reference-HTTP-Tool) | Per-host method matrix + readability extraction |
| `markdown_writer` | [Plugin Reference: markdown_writer](Plugin-Reference-Markdown-Writer) | `.md`-only write/append to an allowlist |
| `shell` | [Plugin Reference: shell](Plugin-Reference-Shell) | Argv-only subprocess with per-command flag allowlist |
| `sqlite` | [Plugin Reference: sqlite](Plugin-Reference-SQLite) | Operator-allowlisted SQLite with sqlglot pre-parse gate |
| `web_search` | [Plugin Reference: web_search](Plugin-Reference-Web-Search) | Provider-agnostic search |
| `pdf_reader` | [Plugin Reference: pdf_reader](Plugin-Reference-PDF-Reader) | Local PDF text extraction |
| `csv_io` | [Plugin Reference: csv_io](Plugin-Reference-CSV-IO) | Read/write/aggregate CSV under allowlist |
| `json_query` | [Plugin Reference: json_query](Plugin-Reference-JSON-Query) | JMESPath filter |
| `rss_reader` | [Plugin Reference: rss_reader](Plugin-Reference-RSS-Reader) | Feed parsing with same SSRF gauntlet |
| `datetime` | [Plugin Reference: datetime](Plugin-Reference-Datetime) | Time / timezone utilities |

### Side effects

| Plugin | Reference | What it does |
|---|---|---|
| `email_sender` | [Plugin Reference: email_sender](Plugin-Reference-Email-Sender) | SMTP send with operator-locked from address + recipient domain allowlist |
| `git` | [Plugin Reference: git](Plugin-Reference-Git) | Structured git operations |
| `image_gen` | [Plugin Reference: image_gen](Plugin-Reference-Image-Gen) | Provider-agnostic image generation |

### External integrations

These plugins push notifications and chat messages outward to external systems. Each is operator-locked: the model never widens hosts / chats, and outbound credentials live in the age vault.

| Plugin | Reference | What it does |
|---|---|---|
| `webhook` | [Plugin Reference: webhook](Plugin-Reference-Webhook) | HMAC-signed POST to allowlisted hosts. Pairs with the inbound trigger system for round-trip integrations (Slack, Zapier, n8n, generic) |
| `telegram_messenger` | [Plugin Reference: telegram_messenger](Plugin-Reference-Telegram-Messenger) | Telegram Bot API: send / edit / delete messages, inline keyboards, typing indicator, set commands. Pairs with the long-poll bot runner ([Telegram Bot Setup](Telegram-Bot-Setup)) for full chatbot UX |

---

## Writing your own

You can — and the built-ins are the reference implementations. See [Plugin Authoring](Plugin-Authoring) for the step-by-step guide. The tl;dr:

1. Subclass nothing — EmberSpark uses structural typing (`Protocol`). Just have the right class attributes and an async `execute`.
2. Declare strict Pydantic schemas with `ConfigDict(extra="forbid")`.
3. Put operator-tunable knobs in `config_schema`. Put per-call model inputs in `input_schema`. When they overlap, the operator wins.
4. Keep `required_permissions` minimal.
5. Register via an entry point in `pyproject.toml`:
   ```toml
   [project.entry-points."spark.plugins"]
   my_plugin = "my_package.my_plugin:MyPlugin"
   ```
6. The agent then references it in `plugins.allow`.

---

## Further reading

- [Using Plugins](Using-Plugins) — the operator's how-to
- [Plugin Authoring](Plugin-Authoring) — writing your own
- [Permissions Guide](Permissions-Guide) — how plugin permissions compose with grants
- [plugin-config.md](https://github.com/Veilfire/EmberSpark/blob/main/docs/plugin-config.md) — the full reference for every built-in's config schema
