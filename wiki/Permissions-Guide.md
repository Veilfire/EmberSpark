# Permissions Guide

This is the long-form practical guide to EmberSpark's permission system. For the abstract model, see [Concepts: Permissions](Concepts-Permissions). For source-level detail, see [docs/tools-and-permissions.md](https://github.com/Veilfire/EmberSpark/blob/main/docs/tools-and-permissions.md).

The rest of this page assumes you already know that EmberSpark has a five-layer permission gate:

1. Plugin allowlist
2. Permission grants
3. Budgets
4. Operator plugin config
5. OS sandbox

Each layer can refuse a tool call. Each is configured separately. **The operator always wins over the model on any overlap.**

---

## A walkthrough with a real tool call

Let's trace a single `http_client.GET` call through every layer. Assume:

- Agent `research-assistant` is loaded
- The agent's YAML has `http_client` in `plugins.allow`
- The agent grants `net.http`, `secrets.read`
- `runtime.max_tool_calls: 25` — and we're at `tool_calls: 3` so far
- The `http_client` plugin config in the UI has:
  - `allow_hosts: ["api.github.com"]`
  - `allowed_methods: ["GET"]`
  - `max_response_bytes: 5000000`

The model generates this tool call:

```json
{
  "tool": "http_client",
  "args": {
    "url": "https://api.github.com/repos/torvalds/linux",
    "method": "GET"
  }
}
```

### Layer 1 — Plugin allowlist

The runtime checks `"http_client" in agent.spec.plugins.allow`. It is. ✓

If it weren't, the runtime would raise:

```
PermissionDenied: plugin 'http_client' not in agent allowlist
```

…and log the attempt at `permission_denied` severity. The model would see the sanitized error in its next message.

### Layer 2 — Permission grants

The plugin declares `required_permissions = {Permission.NET_HTTP, Permission.SECRETS_READ}`.

The runtime checks `{NET_HTTP, SECRETS_READ} ⊆ agent.spec.permissions.grants`. The agent granted both. ✓

If it had missed one, the runtime would raise:

```
PermissionDenied: plugin 'http_client' requires permissions ['net.http']
```

### Layer 3 — Budget

`self.budget.tick_tool()` increments the counter. Current: 4 / 25. Under the ceiling. ✓

If we'd been at 25, the runtime would raise:

```
BudgetExceeded: tool_calls budget exceeded (26/25)
```

…and the run would fail with `error_class=budget_exceeded`.

### Layer 4 — Operator config merge

The runtime calls `load_plugin_config("http_client", HttpClientConfig)`, which pulls the `plugin_configs` row for this plugin. The operator config is:

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

Now `merge_config_and_args` runs:

- For every field in both `config_schema` **and** `input_schema` (the intersection), the operator's value is written into the merged args, replacing whatever the model supplied. `allow_hosts`, `allow_http`, `max_response_bytes`, `connect_timeout_seconds`, `read_timeout_seconds` are all in this set.
- For every field only in `config_schema` (`allowed_methods`, `user_agent`), the value goes into a separate `operator_only` dict that becomes `ctx.plugin_config` inside the plugin.
- For every field only in `input_schema` (`method`, `url`, `headers`, `body`, `json`, `secret_headers`), the model's value passes through.

Final merged args handed to Pydantic:

```json
{
  "method": "GET",
  "url": "https://api.github.com/repos/torvalds/linux",
  "headers": {},
  "secret_headers": {},
  "body": null,
  "json": null,
  "allow_hosts": ["api.github.com"],
  "allow_http": false,
  "max_response_bytes": 5000000,
  "connect_timeout_seconds": 5.0,
  "read_timeout_seconds": 15.0
}
```

**If the model had supplied `allow_hosts: ["evil.example"]`**, the operator's value would still replace it at the merge. The `evil.example` entry never reaches the plugin.

Pydantic validates the merged args against `HttpRequestArgs`. It passes.

### Layer 5 — OS sandbox

The runtime builds a `SandboxPolicy`:

- `ro_paths`: Python prefix, site-packages, plugin module
- `rw_paths`: empty (`http_client` doesn't write files)
- `allow_network`: **True** (because `plugin.needs_network` is True)
- `rlimits`: from `agent.spec.permissions.sandbox` (30 CPU seconds, 512 MB memory, etc.)
- `timeout_seconds`: 60
- `env`: scrubbed

It spawns the sandbox worker:

```
bwrap --unshare-pid --unshare-uts --unshare-cgroup-try --unshare-ipc \
  --ro-bind /usr /usr --ro-bind <python> <python> \
  --proc /proc --dev /dev --tmpfs /tmp \
  --clearenv --setenv PATH /usr/bin:/usr/local/bin \
  --setenv LC_ALL C.UTF-8 --setenv HOME /tmp \
  -- python -I -m spark.sandbox.worker
```

The parent writes the `RequestFrame` (plugin_module, plugin_class, args, secrets, plugin_config) to stdin. The worker loads the plugin, validates the args, calls `execute(args, ctx)`.

Inside the plugin:

1. `validate_url(args.url, HostPolicy.from_list(args.allow_hosts))` runs the full SSRF defense. It resolves `api.github.com`, picks a non-blocked IP, returns a `ResolvedTarget`.
2. The plugin checks `ctx.plugin_config["allowed_methods"]` — `GET` is in the list. ✓
3. The plugin enters `pin_dns(target)` (a context manager that intercepts `socket.getaddrinfo` for `target.host` and returns the pre-validated IP) and makes the httpx request against the original URL — so SNI + TLS cert verification work normally while the TCP connection still goes to the IP that passed the SSRF gauntlet.
4. The response is streamed; when bytes exceed `max_response_bytes`, it stops.
5. The plugin returns an `HttpResponse` with the status, body, and headers.

The worker validates the result against `output_schema` and writes the JSON response frame to stdout. The parent reads the frame, filters it through `filter_for_model` (because `filter_output_before_model=True`), and returns the filtered content to the engine. The engine appends a `tool` message and continues the loop.

Every step is logged. The `span.emitted` event for the `tool_call` span shows the duration. The `tool.invoked` and `tool.result_received` events show the plugin name and the redaction labels.

---

## Where to go when each layer refuses

### Layer 1 refusals

**Symptom:** `PermissionDenied: plugin 'X' not in agent allowlist`

**What it means:** The agent YAML doesn't list this plugin in `plugins.allow`.

**What to do:**

- If the plugin is needed, add it to `plugins.allow` and re-run `spark agent validate`.
- If it's not needed, ignore — the denial is correct.

**Where to look:** Your agent YAML. Also the audit log for the attempt.

### Layer 2 refusals

**Symptom:** `PermissionDenied: plugin 'X' requires permissions ['fs.write']`

**What it means:** The plugin needs a permission the agent hasn't granted.

**What to do:**

- Add the missing permission to `spec.permissions.grants`.
- Or: decide the agent shouldn't have that capability and don't grant it.

**Where to look:** Agent YAML `spec.permissions.grants`. Compare with the plugin's `required_permissions` (visible in the Plugins page).

### Layer 3 refusals

**Symptom:** `BudgetExceeded: tool_calls budget exceeded (26/25)`

**What it means:** The agent hit one of its per-run budget ceilings.

**What to do:**

- If the task is legitimate and just needs more, raise the ceiling (agent YAML → `runtime.max_tool_calls` or `runtime.max_model_calls` or `runtime.max_iterations`).
- If the agent is in a runaway loop, don't raise — investigate why. Open the Run Replay page and look at the iteration timeline.

**Where to look:** Agent YAML runtime block. Guardrails page to see how often budgets trip.

### Layer 4 refusals

**Symptom A:** `UrlDenied: Host 'evil.example' is not in the allowlist`

**What it means:** The operator's plugin config doesn't include the host the model tried.

**What to do:**

- If the host is legitimate, add it to the plugin config via the UI.
- If not, congratulations — the guardrail worked.

**Symptom B:** `PermissionError: http method 'POST' not in allowed_methods ['GET']`

**What it means:** The operator restricted methods; the model tried a forbidden one.

**What to do:**

- Either add the method to the plugin config or let the model adapt.

**Symptom C:** `PermissionError: filesystem plugin configured read_only`

**What it means:** Operator config has `read_only: true`; the model tried to write.

**What to do:**

- Flip `read_only: false` if writes are intended, or have the agent do something else.

**Where to look:** Plugins page in the UI for the specific plugin.

### Layer 5 refusals

**Symptom A:** `SandboxUnavailable: No sandbox backend available`

**What it means:** EmberSpark can't find Bubblewrap / Seatbelt / nsjail. `spark serve` refuses to start in this state.

**What to do:** Install Bubblewrap on Linux or ensure `sandbox-exec` is present on macOS. See [Installation](Installation).

**Symptom B:** `SandboxTimeout: Sandboxed plugin 'X' exceeded 60s`

**What it means:** The sandbox child process exceeded its wall-clock limit.

**What to do:** Raise `permissions.sandbox.timeout_seconds` in the agent YAML if the operation is legitimately slow, or investigate why the plugin is hanging.

**Symptom C:** Kernel-level refusals (e.g. `EACCES` from inside the plugin)

**What it means:** The plugin tried to access something the sandbox didn't bind-mount. Usually this is a bug in the plugin, not EmberSpark.

**What to do:** Check `rw_paths` and `ro_paths` for the agent, make sure the paths the plugin needs are in `permissions.filesystem.allow_paths`.

---

## Tightening an agent in practice

Here's a 10-step checklist for narrowing an existing agent:

1. **Open the agent YAML.** Is every plugin in `plugins.allow` actually used? If not, remove it.
2. **Check `permissions.grants`.** Remove any permission not required by an allowlisted plugin.
3. **Check `runtime.max_iterations` / `max_model_calls` / `max_tool_calls`.** Set them to the smallest values that still let real tasks complete.
4. **Check `runtime.max_runtime_seconds`.** Set a wall-clock ceiling the agent should never exceed.
5. **Check `permissions.filesystem.allow_paths`.** Narrow it to just the directories the agent needs to touch.
6. **Check `permissions.network.allow_hosts`.** Advisory operator-side allowlist; narrow it to exactly the hosts the agent calls. (Per-call SSRF / hostname validation lives inside each plugin via `HostPolicy`; the `net.http` grant alone gates whether bwrap shares the network namespace.)
7. **Check `permissions.sandbox.cpu_seconds` / `memory_mb`.** Tighten rlimits for cheap operations; loosen for heavy ones.
8. **Open the Plugins page in the UI.** For every allowlisted plugin, set the narrowest config that works. Use `read_only` where possible. List only the methods, hosts, commands you need.
9. **Set up a cost budget.** Global daily hard stop as a safety net. Per-agent budgets for specific scopes.
10. **Look at the audit log.** If there are recent `permission_denied` entries, understand why each happened.

Any step you skip is a surface you're accepting. That might be the right call for your situation — but it should be a decision, not an oversight.

---

## Testing denials

A quick way to verify each layer is actually refusing what you expect:

### Layer 1 test

1. Temporarily remove a plugin from `plugins.allow`.
2. Send the agent a task that would use it.
3. Confirm the audit log shows `permission_denied` with the plugin name.
4. Restore the plugin.

### Layer 2 test

1. Temporarily remove a permission from `grants`.
2. Send a task.
3. Confirm the audit log shows the missing-permission error.
4. Restore the grant.

### Layer 3 test

1. Set `runtime.max_tool_calls: 1`.
2. Send a task that would use 2+ tool calls.
3. Confirm `BudgetExceeded` on the second call.
4. Restore the limit.

### Layer 4 test

1. In the Plugins page, narrow a plugin (remove a host, disable a command, set `read_only`).
2. Send a task that would use the removed capability.
3. Confirm the denial.
4. Optionally widen back.

### Layer 5 test

Click **Run self-test** in the Security Center → Sandbox tab. It verifies the sandbox backend is reachable and produces a short report. You can also look at the Ops page for the backend name.

---

## Further reading

- [Concepts: Permissions](Concepts-Permissions) — the abstract model
- [Security Center Guide](Security-Center-Guide) — the UI for editing permissions
- [Failure Inspector](Failure-Inspector-Guide) — when a permission/budget/sandbox/network/filesystem gate refuses an operation, the inspector surfaces the matched element + ranked tuning options + risks, and deep-links to the page that fixes it
- [Using Plugins](Using-Plugins) — the per-plugin operator workflow
- [docs/tools-and-permissions.md](https://github.com/Veilfire/EmberSpark/blob/main/docs/tools-and-permissions.md) — source-level reference
