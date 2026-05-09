# Concept: Permissions & Grants

EmberSpark treats permission as a **composition** — not a single switch. Five layers stack, each can refuse a tool call, and the operator controls all five.

This page is the conceptual overview. For a deep dive with examples, see the [Permissions Guide](Permissions-Guide).

---

## The five layers

```
 Model generates a tool call
           │
           ▼
  ┌────────────────────────┐
  │ 1. Plugin allowlist    │  spec.plugins.allow includes the plugin?
  └────────────────────────┘
           │ yes
           ▼
  ┌────────────────────────┐
  │ 2. Permission grants   │  plugin.required_permissions ⊆ spec.permissions.grants?
  └────────────────────────┘
           │ yes
           ▼
  ┌────────────────────────┐
  │ 3. Budget              │  BudgetGuard.tick_tool() under ceiling?
  └────────────────────────┘
           │ yes
           ▼
  ┌────────────────────────┐
  │ 4. Operator config     │  Merge operator config; operator wins on overlap.
  │    merge               │  Schema validation passes?
  └────────────────────────┘
           │ yes
           ▼
  ┌────────────────────────┐
  │ 5. OS sandbox          │  Kernel enforces bind mounts, rlimits, netns.
  └────────────────────────┘
           │ yes
           ▼
     Tool actually runs
```

Any layer can say "no" and the call is refused with a classified error (`permission_denied`, `budget_exceeded`, `network_denied`, `sandbox_denied`, ...). The failure is audited.

---

## Layer 1 — The plugin allowlist

Each agent's YAML declares which plugins it is allowed to call:

```yaml
spec:
  plugins:
    allow:
      - http_client
      - markdown_writer
```

This is the coarsest gate. A plugin not in this list is simply not callable by this agent — the runtime doesn't even try.

**Omit plugins you don't need.** If your agent only writes markdown, don't put `shell` in the allowlist "just in case." The safest plugin is one that isn't there.

---

## Layer 2 — Permission grants

Every plugin declares what permissions it **requires** to function:

| Plugin | Required permissions |
|---|---|
| `filesystem` | `fs.read`, `fs.write`, `fs.list` |
| `http_client` | `net.http`, `secrets.read` |
| `markdown_writer` | `fs.write` |
| `shell` | `subprocess` |
| `sqlite` | `fs.read` |

The agent must grant **every** required permission or the plugin is refused:

```yaml
spec:
  permissions:
    grants:
      - fs.write
      - net.http
      - secrets.read
```

If the agent allows `shell` but doesn't grant `subprocess`, every shell call is refused at the second gate. You still see the attempt in the audit log — which is useful if you want to catch the model reaching for capabilities it doesn't have.

### The permission enum

| Permission | Scope |
|---|---|
| `fs.read` | Read, list, stat on the filesystem plugin |
| `fs.write` | Write, append, create files |
| `fs.list` | List directories (read-only subset) |
| `net.http` | Outbound HTTPS via http_client |
| `subprocess` | Run subprocesses via the shell plugin |
| `secrets.read` | Resolve a declared secret from the age vault (or env fallback) |

Granting `fs.list` without `fs.read` lets an agent *enumerate* but not *open* files. That's intentional — a dry-run agent might only need that.

---

## Layer 3 — Budgets

Budgets are enforced by [`BudgetGuard`](https://github.com/Veilfire/EmberSpark/blob/main/spark/plugins/tool_runtime.py) at three counters + one wall clock:

- `max_iterations` — LangGraph loop iterations
- `max_model_calls` — total llm.ainvoke() calls
- `max_tool_calls` — total tool invocations
- `max_runtime_seconds` — wall-clock ceiling via `asyncio.wait_for`

Hitting any ceiling raises `BudgetExceeded`, which is classified and logged. The run is marked failed.

Cost budgets are a separate system — they fire **before** the run starts. See [Cost & Budgets](Cost-And-Budgets).

---

## Layer 4 — Operator plugin config

Every plugin has two schemas: `input_schema` (what the model sends) and `config_schema` (what the operator sets). At the merge step in the tool runtime:

- Fields in both schemas — **operator wins**. The model's value is replaced.
- Fields only in `config_schema` — pass to the plugin via `ctx.plugin_config`.
- Fields only in `input_schema` — pass through as-is.

This is the layer that lets you narrow an agent without touching YAML. Set `http_client.allow_hosts: ["api.github.com"]` in the Plugins page, and any attempt by the model to pass a different host list is silently replaced with the operator's value.

See the [Permissions Guide](Permissions-Guide) for a step-by-step walkthrough of the merge.

---

## Layer 5 — The OS sandbox

The final gate runs in the kernel. Every tool call is dispatched as a child process under Bubblewrap (Linux), nsjail (Linux strict), or Seatbelt (macOS). The sandbox enforces:

- **Bind-mount scoping** — the child can only see what's bind-mounted. `~/.ssh`, `~/.aws`, etc. are invisible.
- **Rlimits** — CPU seconds, memory, file descriptors, process count.
- **Network isolation** — if `needs_network=False`, the child has no network namespace. Any socket call fails at the kernel.
- **Environment scrub** — no parent env vars. Secrets come in via stdin, not `/proc/<pid>/environ`.

The sandbox is **mandatory**. `spark serve` refuses to start without a working backend. There is no "run without sandbox" escape.

---

## Composition example

Let's trace a successful call.

Agent YAML:

```yaml
spec:
  plugins:
    allow: [http_client, markdown_writer]
  permissions:
    grants: [net.http, secrets.read, fs.write]
    filesystem:
      allow_paths: [~/workspace]
    network:
      allow_hosts: [api.github.com]
    sandbox:
      cpu_seconds: 30
      memory_mb: 512
  runtime:
    max_tool_calls: 25
```

Plugin config (set via UI):

```json
{
  "http_client": {
    "allow_hosts": ["api.github.com"],
    "allowed_methods": ["GET"]
  }
}
```

Model generates:

```json
{"tool": "http_client", "args": {"url": "https://api.github.com/foo", "method": "GET"}}
```

Walk the layers:

1. **Layer 1** — `http_client` is in `allow`. ✓
2. **Layer 2** — `{net.http, secrets.read}` ⊆ `{net.http, secrets.read, fs.write}`. ✓
3. **Layer 3** — tool_calls = 1 / 25. ✓
4. **Layer 4** — Operator config merge: `allow_hosts` stays `["api.github.com"]`. `method=GET` is in `allowed_methods`. Pydantic validation passes. ✓
5. **Layer 5** — Sandbox spawned: Python RO, `~/workspace` RW-bind, network namespace shared (plugin needs network), rlimits applied. ✓

Plugin runs. Response returned. Filtered through privacy. Delivered to engine.

If any layer said no, the call would fail at that layer with the appropriate error class. The attempt would be audited. The model would receive a sanitized `"tool error [permission_denied]: ..."` message.

---

## What the operator sees

Every permission denial writes an audit entry with `kind=security.permission_denied` and severity `info` (for routine denials) or `elevated` (for denials that might indicate probing). The Guardrails dashboard aggregates these by category so you can see at a glance:

```
permission_denied:    4
sandbox_denied:       0
budget_exceeded:      1
plugin_hash_changed:  0
```

Click any category to jump into a pre-filtered audit log view.

---

## Further reading

- [Permissions Guide](Permissions-Guide) — deep dive with examples for every layer
- [Error Codes](Error-Codes) — stable identifiers every classified denial carries
- [Concepts: Plugins](Concepts-Plugins) — what a plugin is
- [Concepts: The Sandbox](Concepts-Sandbox) — how layer 5 actually works
- [Security Center Guide](Security-Center-Guide) — the UI for editing permission settings
- [tools-and-permissions.md](https://github.com/Veilfire/EmberSpark/blob/main/docs/tools-and-permissions.md) — source-level reference
