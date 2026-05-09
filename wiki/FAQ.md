# Frequently Asked Questions

## General

### Why single-agent?

Most agent frameworks default to multi-agent orchestration. EmberSpark is deliberately single-agent per `spark serve` instance for two reasons:

1. **Bounded autonomy is easier to reason about when there's one thing to bound.** Every permission, budget, and audit entry is attributable to one agent.
2. **You know what the agent is at a glance.** The persona page shows the one voice. The plugins page shows the one set of capabilities. No task metadata required.

If you need two agents with genuinely different behavior, run two `spark serve` instances with different state directories. Multi-agent orchestration with sub-agents is explicitly future work.

### Why not just use [LangChain / AutoGen / CrewAI]?

Those frameworks are optimized for "maximum autonomy." EmberSpark is optimized for "bounded autonomy." The tradeoff is real: EmberSpark is a bit more work to set up because you have to be explicit about permissions, but it's much easier to reason about in production.

You can absolutely run LangChain-style agents on top of LangGraph (EmberSpark uses LangGraph internally). But the frameworks above don't give you:

- Mandatory OS sandboxing for every tool call
- Operator plugin config that overrides model args
- A five-layer permission gate
- Automatic PII redaction on everything (logs, memory, tool outputs)
- Hash-chained log retention
- A security center UI

### Can I use EmberSpark to run an agent for production work?

Alpha software. Use your judgment. The safety posture is well-thought-through, but the code is fresh. If you run it in production, test thoroughly first and keep the scope narrow.

### Is EmberSpark "an autonomous agent"?

It runs agents with bounded autonomy. Whether you call that "autonomous" depends on your definition. The agent makes real choices inside the envelope you define, but the envelope is tight by default.

---

## Security

### What's the threat model?

See [docs/security-posture.md](https://github.com/Veilfire/EmberSpark/blob/main/docs/security-posture.md) for the full version. Short version:

- The model may be hostile
- Plugins may be buggy or compromised
- Tool outputs may be attacker-controlled
- Secrets must never reach the model, memory, or logs by default
- The host is trusted (we don't defend against hostile root)
- The operator is trusted but fallible

### Can the model bypass the permission system?

Layer by layer:

- **Layer 1 (allowlist)** — no. The runtime literally doesn't try to load plugins that aren't allowlisted.
- **Layer 2 (grants)** — no. The runtime checks permission subset before calling the plugin.
- **Layer 3 (budgets)** — no. `BudgetGuard` is process-state and isn't exposed to the model.
- **Layer 4 (operator config)** — no. The merge logic always gives operator values precedence. The model can send `allow_hosts: ["evil.example"]` all day; the merge replaces it before the plugin sees it.
- **Layer 5 (sandbox)** — in theory, a kernel exploit. In practice, no.

Could a *bug* in EmberSpark's code bypass a layer? Sure. That's why there are five of them. A bug in one layer is caught by the next.

### Is the sandbox actually mandatory?

Yes. `spark serve` checks for a working sandbox backend at startup and **refuses to start** if none is available. You can't run EmberSpark without Bubblewrap / Seatbelt / nsjail. This is deliberate — a missing sandbox is a silent downgrade in safety, and we don't do silent.

### Can I turn off redaction?

Partially. You can set `privacy_mode: regex_only` to skip the Presidio NER layer (useful for lean installs). You can set `logging.raw_prompts: true` to bypass prompt redaction in logs (audited at `critical`, strongly discouraged).

You **cannot** turn off the pattern-scrubbing layer in logs — that's hard-coded into the structlog processor chain. Secret values tracked by the SecretManager are always replaced with `***`.

### How do I rotate credentials?

```bash
spark serve --rotate-credentials
```

Save the printed credentials immediately. This invalidates the previous bcrypt hash.

---

## Plugins

### Why are the shell and sqlite plugins disabled by default?

They're the highest-surface-area plugins. Ship-disabled means an operator has to explicitly opt in — for every command in the shell case, for every database in the sqlite case. Installed ≠ usable is EmberSpark's default posture for anything with real reach.

### Can I use shell to run arbitrary commands?

No. The shell plugin uses an argv-only allowlist with per-command flag sets. You register named commands like `git-log` → `["git", "log", "--oneline"]` with allowed flags, and the model can only invoke those exact commands with those exact flags and a capped number of positionals. Shell metacharacters in positionals are rejected. No shell interpretation at any point.

If you want "arbitrary commands," register `bash` with allowed_flags `["-c"]` and allowed_positional_count `1` — and then accept that the sandbox and every other layer is the only thing protecting you. We don't recommend this.

### Can plugins write to each other's files?

A plugin's sandbox bind mounts are derived from the agent's `permissions.filesystem.allow_paths`. All plugins running under the same agent see the same bind mounts. So yes, in principle, plugin A can write a file that plugin B then reads. This is usually desirable (e.g. an http_client that writes a payload, a markdown_writer that reads and transforms it).

If you want to separate them, use different agents with different `allow_paths`, or use the filesystem plugin's `read_only` switch + a separate markdown_writer with narrow `allow_paths` for the output.

### Does EmberSpark support custom plugins?

Yes. See [Plugin Authoring](Plugin-Authoring) for the guide. You register via entry points in your package's `pyproject.toml`, and EmberSpark's `default_registry` auto-discovers them. But the plugin still has to be allowlisted, granted, and configured before it's usable — no "install & it works" shortcut.

---

## Memory + learning

### Can I export my agent's learned memory?

Yes. The Memory page has a list view and an API endpoint (`/api/memory/long-term`). For a bulk export:

```bash
sqlite3 ~/.spark/spark.db \
  ".mode json" \
  "SELECT * FROM long_term_memory_index" > memories.json
```

The vector data (embeddings) is in `~/.spark/chroma/`. You can load it with the Chroma Python client.

### Can I import memories from another agent?

Not via a built-in command, but you can copy rows between SQLite databases and copy the Chroma namespace directory. Careful: the sentence-transformers embedding model must match.

### How do I clear all memory?

```bash
rm -rf ~/.spark/chroma
sqlite3 ~/.spark/spark.db "DELETE FROM long_term_memory_index"
```

Restart the runtime. The agent starts fresh.

### Why doesn't the agent remember things from one run to the next?

Check:

1. `spec.memory.long_term_memory.enabled: true` in the agent YAML
2. `spec.runtime.reflection: true` — reflection is what promotes memories
3. The run actually succeeded — failed runs don't promote memories
4. The Memory page shows records in the agent's namespace

If reflection is disabled or the runs are failing, no memories are written.

---

## Operations

### Where does EmberSpark store data?

All under `~/.spark/`:

- `spark.yaml` — config
- `spark.db` — SQLite (agents, tasks, runs, memory index, plugin configs, personas, audit log)
- `chroma/` — Chroma persistent vector store
- `logs/` — JSONL logs in hot/warm/cold/archive buckets
- `web-token` — headless auth token
- `web-credentials.json` — bcrypt hash of the UI password
- `scheduler.db` — APScheduler job store
- `firecracker/` — microVM artifacts (if using that daemon mode)
- `secrets.age` — age-encrypted vault (H1.3)
- `age_identity.key` — age identity, mode 0600 (or `.age` variant when passphrase-wrapped)

Secrets live in the age vault managed by `spark secrets` — see the [Secrets Guide](Secrets-Guide).

### Can I run multiple EmberSpark instances on the same host?

Yes — set different state directories via `HOME` env var or symlink tricks. Each instance needs its own port. Each has its own credentials, its own DB, its own logs. They can't share state.

### Can I run EmberSpark in Docker / Firecracker?

Yes, both are supported daemon modes. See [Deployment Guide](Deployment-Guide).

### Does EmberSpark support Windows?

No. The mandatory OS sandbox requires Bubblewrap (Linux) or Seatbelt (macOS). WSL2 gives you a Linux environment where EmberSpark works.

### How much does it cost to run?

EmberSpark itself is free. The LLM provider is where costs come from. OpenAI/Anthropic/OpenRouter charge per token; Ollama is free (local). Cost budgets in the UI let you cap per-agent, per-provider, global.

A typical research task with Anthropic Opus might be $0.10–$0.50. With Haiku, ~$0.01. With Ollama, $0. Set monthly budgets to avoid surprises.

---

## Developer

### Can I contribute?

Yes. See [Contributing](Contributing). The code is Apache 2.0.

### Where's the issue tracker?

GitHub Issues on the repository. For security issues, email `security@spark.dev` (placeholder) instead of filing public issues.

### Is there a roadmap?

See [spec.md](https://github.com/Veilfire/EmberSpark/blob/main/spec.md) for the original scope and [README.md](https://github.com/Veilfire/EmberSpark/blob/main/README.md) for the current state. Explicitly deferred features:

- Multi-agent orchestration / sub-agents
- OpenTelemetry bridge
- Log forwarders (OTLP, syslog, S3)
- Light theme for the web UI

These may or may not happen based on real usage data.
