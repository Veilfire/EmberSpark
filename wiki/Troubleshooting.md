# Troubleshooting

Common problems and their fixes. Organized by symptom so you can ctrl-F.

---

## Installation

### `spark doctor check` says "sandbox unavailable"

You don't have a working sandbox backend.

- **Linux**: `sudo apt install bubblewrap` (or your distro's equivalent), verify `which bwrap` returns a path
- **macOS**: `which sandbox-exec` should return `/usr/bin/sandbox-exec` — it's built in. If missing, you're on an unusually old macOS or have disabled system binaries; neither is fixable from EmberSpark's side.
- **Windows**: not supported. Use WSL2 for a Linux environment.

After fixing, `spark doctor check` should report the backend by name.

### `ImportError: No module named 'langchain_openai'`

You installed EmberSpark without the provider extra. Fix:

```bash
pip install -e '.[openai,anthropic,openrouter,ollama,web,dev]'
```

Pick whichever extras you actually use. `web` is the most important one if you plan to use the UI.

### Presidio errors on first run

Either the spaCy model isn't downloaded, or Presidio isn't installed.

```bash
python -m spacy download en_core_web_lg
```

If that fails, verify Presidio is present: `python -c "from presidio_analyzer import AnalyzerEngine; AnalyzerEngine()"` — should not error.

If you want to skip Presidio entirely (lean install), set `privacy_mode: regex_only` in your agent YAML.

---

## Startup

### `spark serve` exits immediately with "web UI is disabled"

Your `~/.spark/spark.yaml` has `spec.web.enabled: false` (the default). Flip it to `true` and re-run.

### `spark serve` exits with a sandbox unavailable error

See the installation section above. The sandbox check runs at startup — EmberSpark refuses to start without a working backend.

### `spark serve` starts but no credentials appear

Check stderr (not stdout). Credentials are printed to stderr so they don't land in piped stdout output. If you're running via systemd, they'll be in the journal:

```bash
journalctl --user -u spark -n 50
```

If credentials really aren't appearing, check if `~/.spark/web-credentials.json` already exists. If `rotate_on_startup: false` and credentials exist, EmberSpark reuses them without printing. Either flip `rotate_on_startup: true` and restart, or run `spark serve --rotate-credentials`.

### Web UI opens but login fails with "invalid credentials"

The credentials you entered don't match the bcrypt hash in `~/.spark/web-credentials.json`. Either:

- You entered the wrong password (characters are case-sensitive)
- The credentials you saved are from a previous startup and have since been rotated

Re-run `spark serve --rotate-credentials` to mint a new pair. Save them this time.

---

## During a run

### Task fails with `permission_denied: plugin 'X' not in agent allowlist`

The plugin is not in the agent YAML's `spec.plugins.allow` list. Add it if you want the agent to use it, or accept the denial as correct behavior.

### Task fails with `permission_denied: plugin 'X' requires permissions ['Y']`

The plugin needs a permission the agent hasn't granted. Add the missing permission to `spec.permissions.grants`.

### Task fails with `network_denied: Host 'X' is not in the allowlist` or `URL_DENIED`

The agent YAML's `spec.permissions.network.allow_hosts` is now **advisory** — only the `net.http` grant gates whether the sandbox shares the network namespace. The hostname check that's actually denying you lives inside the plugin:

- `http_client` — set `http_client.allow_hosts` in the Plugins page.
- `http_tool` — add a named-host rule (or rely on the default `*` GET-only fallback rule).
- `rss_reader` / `webhook` / `email_sender` — add the host in the Plugins page.
- `web_search` — the provider host is hardcoded; if `URL_DENIED` fires here it's almost always a DNS resolution failure inside the sandbox (verify `/etc/resolv.conf` is readable on the host).

### Task fails with `path_denied: Path /X is outside allow list`

`filesystem`, `csv_io`, `pdf_reader`, and `markdown_writer` all fall back to `ctx.scratch_path` + `ctx.deliverables_path` when `allow_paths` is empty (data volume must be enabled). If you've configured explicit `allow_paths`, the fallback is bypassed and only the configured paths are valid. Verify the agent YAML's `spec.permissions.filesystem.allow_paths` AND the plugin config in the Plugins page agree, and check that symlinks in the path don't resolve somewhere unexpected (EmberSpark follows symlinks once during resolution).

### Task fails with `budget_exceeded`

The run hit an iteration / model call / tool call ceiling. Either raise the ceiling in `runtime.max_*` or investigate why the run is using so much budget. Check the Run Replay flame graph to see where it spent its iterations.

### Task stuck in "running" state forever

The wall-clock timeout should kill it eventually (`max_runtime_seconds`). If it doesn't, the process might be wedged — check Ops → live log tail for activity. Worst case, `pkill -TERM spark` and restart.

### Task refused because "EmberSpark is frozen"

You clicked Freeze in the Security Center → Global Posture tab. Unfreeze from the same tab.

### Task defers with "budget hard stop"

A cost budget is preventing fires. Open Cost & Budgets, either raise the limit, delete the budget, or wait until the period resets (daily/weekly/monthly).

### Sandbox subprocess fails with `Unexpected capabilities but not setuid, old file caps config?`

bwrap detected that the parent process has ambient Linux capabilities and refused to run. This happens when a Docker / Podman compose file adds `cap_add: SYS_ADMIN` (or any other cap) — bwrap creates **unprivileged** user namespaces and aborts when it sees unexpected caps. Fix the compose file: `cap_drop: ALL`, plus `security_opt: [seccomp=unconfined, apparmor=unconfined]`. See the Deployment Guide for the full pattern.

### Sandbox subprocess fails with `Can't mount proc on /newroot/proc: Permission denied`

You're inside a nested user namespace (EmberSpark running in a container) and the kernel won't let bwrap create a fresh `/proc` mount. The current backend auto-detects this (`/run/.containerenv` / `/.dockerenv`) and bind-mounts `/proc` + `/dev` instead. If you still see this error, check that one of those marker files exists in the container — if it doesn't, the auto-detect missed.

### Sandbox plugin fails with `DNS resolution failed` or `CERTIFICATE_VERIFY_FAILED: IP address mismatch`

Networked plugins now bind `/etc` read-only into the bwrap mount namespace (so glibc finds `resolv.conf` and OpenSSL finds the CA bundle) and use `pin_dns(target)` (so the URL keeps its hostname for SNI/cert verification while DNS is pinned to the validated IP). If you're seeing one of these errors with a recent build, the plugin is probably using the old "rebuild URL with IP literal" pattern — port it to `pin_dns` (see [Plugin Authoring](Plugin-Authoring) → "Don't fetch URLs directly").

---

## Plugins not doing what you expect

### "I edited the plugin config but the agent still uses the old value"

The config change takes effect on the next tool call. If a run is in progress, its next call picks up the new config. If no run is in progress, start one and see.

Double-check:

- You clicked **Save**, not just typed into the field
- The audit log shows a recent `plugin.config.update` entry
- The right plugin — agent YAML `plugins.allow` has to include this plugin

### "Shell plugin isn't running anything"

Two places to check:

1. `shell.enabled` must be `true` (default `false`)
2. `shell.allowed_commands` must contain the command you're trying to run

Both are operator-edited in the Plugins page.

### "Sqlite plugin says banned keyword"

The SQL string contains `PRAGMA`, `ATTACH`, `DETACH`, `VACUUM`, `CREATE`, `DROP`, `ALTER`, `REINDEX`, or `ANALYZE`. These are refused even in `read_write` mode. Rewrite the query to avoid them — DDL / PRAGMA is not exposed to the agent.

### "Markdown writer refuses to write"

Check:

- The path ends in `.md` or `.markdown` (the Pydantic validator enforces this)
- The path is in `allow_paths` (both agent YAML and plugin config should agree)
- The parent directory exists and isn't a symlink (the plugin won't `mkdir -p`)
- `allow_overwrite: true` if you're writing a new file or `allow_append: true` if appending

---

## Persona not picking up changes

### "I clicked Save & Activate but the old persona is still in use"

The change applies on the **next** model call, not retroactively. An in-flight call finishes with the old persona.

Verify:

- Audit Log shows a recent `persona.activated` entry
- Persona page shows the active badge on the persona you think
- You sent a new chat message (or started a new run) **after** activation

If all three look right but you're still seeing old behavior, start a brand-new chat session to rule out client-side caching.

---

## Memory + learning

### "The agent isn't learning from past runs"

Check:

- The agent has long-term memory enabled: `spec.memory.long_term_memory.enabled: true`
- There are records in the Memory page's long-term index for this agent's namespace
- Reflection is enabled: `spec.runtime.reflection: true`

Reflection only runs on **successful** runs. If the run failed, no lessons are promoted.

Playbooks similarly only reinforce on success. If you see zero playbooks for an agent, it might be because every run so far has failed.

### "The memory index has stale entries I want to remove"

Open the Memory page, filter by namespace, click Delete on the row. Removes from both the index and the Chroma collection.

---

## Log / audit issues

### `spark logs verify` says "chain broken"

Someone (possibly you, possibly not) modified a rotated log file. Figure out which:

```bash
ls -la ~/.spark/logs/hot/ ~/.spark/logs/warm/ ~/.spark/logs/cold/
```

If the mtime on a file looks suspicious, investigate. Common benign causes:

- You rsync'd `~/.spark/logs/` to another host and back (changes mtimes)
- A filesystem snapshot restore dropped a file back in
- You edited a log manually to redact something (don't — use the audit log for the fact, and don't touch the source)

If you can't figure it out, the chain verdict gives you the exact broken file so you can investigate its provenance.

### "I can't find the event I'm looking for in the audit log"

The Audit Log page UI filters are `kind` (substring) and `min_severity`. For more complex queries, go direct:

```bash
sqlite3 ~/.spark/spark.db \
  "SELECT ts, actor, kind, target, severity, reason FROM audit_log \
   WHERE kind LIKE '%skill%' ORDER BY ts DESC LIMIT 50"
```

---

## Performance

### "The sandbox spawn is slow"

Bubblewrap spawns are typically 30–100 ms. If you're seeing seconds, something is wrong:

- Check if your home directory is on a slow filesystem (NFS, sshfs, etc.) — the bind mounts are slow
- Try `nsjail` as the backend — it can be faster for cold-start

### "Memory retrieval is slow"

The sentence-transformers model is loaded lazily on first use. First retrieval is slow (~2–5 s for model load), subsequent ones should be fast. Run `spark doctor check` to prewarm.

### "The LLM call is slow"

That's the provider, not EmberSpark. Check the Run Replay flame graph — the `plan` span is where the provider RTT lives. If `plan` is slow, it's the model. EmberSpark can't speed it up.

---

## Webhook triggers

### "Webhook fires return 401 with a valid signature"

Three causes, in order of frequency:

1. **Wrong auth_mode.** GitHub / Stripe / Linear / Vercel all use `hmac_sha256` (raw-body HMAC). Slack uses `hmac_sha256_slack` (`v0:<ts>:<body>` with replay window). Pick the right one when creating the trigger.
2. **Wrong header.** EmberSpark accepts `X-Hub-Signature-256` (GitHub default), `X-Spark-Signature-256`, or `X-Signature-Sha256` for `hmac_sha256`. For Slack mode, `X-Slack-Signature` + `X-Slack-Request-Timestamp`. Other vendor-specific headers (Linear's `Linear-Signature`, Twilio's `X-Twilio-Signature`) need an upstream rewriter.
3. **Body mutation in transit.** A reverse proxy that re-encodes JSON, normalises line endings, or strips trailing whitespace breaks HMAC. Verify the proxy passes the body through verbatim.

The audit log row `trigger.fired` lands on success; failed verifies increment `failed_verify_count` on the trigger row (visible in the Scheduler page).

### "Trigger keeps locking out"

After 10 consecutive bad signatures the trigger locks for 15 min — this defends against credential-stuffing on a leaked endpoint URL. If the lockout is from a real client misconfiguration, fix the client and wait for the lockout to expire (or delete + recreate the trigger).

### "Slack URL verification fails"

Make sure the trigger's `auth_mode` is `hmac_sha256_slack`. The URL-verification challenge is gated to that mode — bearer / standard hmac_sha256 triggers reject it (deliberately, so the endpoint isn't a public echo server).

### "Webhook returns 503"

The HMAC secret lives in the age vault. If the vault isn't unlocked at the time the webhook fires, EmberSpark refuses verification. `spark secrets set …` after vault init makes the secret available; restart the server if needed.

### "Webhook returns 413"

Body exceeded the 5 MB cap. Most providers stay well under (GitHub 25 MB max, Slack 3 KB). If you legitimately need a larger payload, this is a deliberate design decision — push the bulk somewhere else (S3, deliverables) and let the webhook carry just the pointer.

---

## Telegram bot

### "Bot doesn't respond"

Check the server log for `telegram_bot.poll_failed`, `telegram_bot.token_missing`, or `telegram_bot.chat_blocked`. Most common causes:

- Token not set: `spark secrets set telegram_bot_token`.
- Chat ID not in `bindings` (the bot silently ignores messages from unbound chats).
- User ID not in the binding's `allow_user_ids`.
- In groups, BotFather privacy mode is enabled — the bot only sees @-mentions. Set `/setprivacy` → Disable in BotFather.

### "Bot replies say 'Unknown command /run'"

`/run` is opt-in per binding. Set `allow_run_tasks: [task1, task2]` on the binding to enable it for specific tasks. Empty list = `/run` disabled.

### "Bot says 'Run … is not under this chat's bound agent. Cancel refused.'"

`/cancel` is scoped to the binding's agent. A chat bound to `research-assistant` can't cancel a `code-reviewer` run — by design. Bind one chat per agent for cross-agent control, or run the cancel from the web UI / CLI.

### "Bot sends typing… but never replies"

The agent fired but didn't call `telegram_messenger` to reply. Two common causes:

- `telegram_messenger` not in the agent's `plugins.allow`.
- `allow_chat_ids` doesn't include the source chat (the plugin refuses to send).

---

## Getting help

1. Check the [FAQ](FAQ)
2. Check [docs/security-posture.md](https://github.com/Veilfire/EmberSpark/blob/main/docs/security-posture.md) for security-related questions
3. Check the audit log and Guardrails dashboard for runtime-level issues
4. File an issue with:
   - The output of `spark doctor check`
   - Your `~/.spark/spark.yaml` (redact any secrets if you used env fallback)
   - Relevant JSONL log excerpts from `~/.spark/logs/hot/`
   - The specific error message
